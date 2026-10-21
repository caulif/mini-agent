TEXT = {
8: dict(title='Failures and retries', subtitle='Retry the request, preserve completed work',
 goal='Add bounded retries to one model request while preserving completed tool operations, rather than restarting the entire agent task.',
 problems=['A tool has already written report.txt when the next model request returns 503. The request needs recovery; the network error does not undo the file change.',
           'An HTTP success can still contain invalid JSON or incomplete tool arguments. The response must be usable before execution continues.'],
 theory='''<p>A normal tool loop contains two distinct actions: <strong>the model returns an intention, and the host executes it</strong>. They are adjacent in the code, but they cannot share an unrestricted retry policy. A request can be sent again under an explicit policy; a completed file write has already changed external state.</p>
<h4 class="mini">Keep recovery at the layer that failed</h4>
<p>Consider changing report.txt and then summarizing the result. The model first returns write, the program updates the file and saves the tool result, and a second request carries that history. If this second request receives 503, sending the same history again does not require another write.</p>
<pre class="ascii">First request → write intention → file changed → tool result saved
                                                     ↓
                                              Second request
                                              503 → wait → retry
                                                     ↓
                                                Final answer</pre>
<p>A file write is a <strong>side effect</strong>: it changes state outside the function. The term does not mean that the operation is harmful. Repeating an append, for example, can create two records. The advanced write tool overwrites files, but that does not make replaying tools a general recovery strategy.</p>
<h4 class="mini">Failures have different meanings</h4>
<p>A 401 usually requires correcting credentials, while a 429 may require waiting. A timeout only establishes that the client obtained no usable response. The server might already have processed the request, so no result does not establish no execution.</p>
<div class="tw"><table class="plain"><tr><th>Observation</th><th>Current policy</th><th>Reason</th></tr><tr><td>429, 502, 503, 504</td><td>Retry within the attempt budget</td><td>Explicit status allowlist</td></tr><tr><td>Other HTTP errors</td><td>Stop and preserve the status</td><td>No assumption that repetition fixes the problem</td></tr><tr><td>Timeout or disconnect</td><td>Unknown outcome; no automatic retry</td><td>Server processing cannot be confirmed</td></tr><tr><td>Invalid or truncated response</td><td>Reject before the tool loop</td><td>No usable intention was received</td></tr></table></div>
<h4 class="mini">Attempts, backoff, and total duration differ</h4>
<p>max_retries=2 means one initial attempt plus at most two retries: three sends in total. Backoff starts from 0.5 seconds, grows exponentially, and is multiplied by a random factor from 0.5 to 1.0 to reduce synchronized retries. Retry-After is a lower bound: a requested 30-second wait exceeds the local 10-second cap, so the implementation stops rather than sending early.</p>
<p>The cap limits one backoff, not the whole task. Each request has its own timeout. Signal is checked during backoff and after a response arrives; a blocking urllib read still cannot guarantee immediate cancellation.</p>''',
 choices=['Retry only one HTTP operation; leave tools in the outer loop so the call structure limits replay.', 'Use RequestFailure categories and status codes instead of guessing from arbitrary exception strings.', 'Validate a successful HTTP body before returning it; protocol validity and business input validity are separate checks.'],
 files=['Shared attempt and delay budgets', 'Failure types, backoff, and cancellation', 'HTTP classification and response validation', 'Delegate sending to the transport layer', 'Regression checks for requests and side effects'],
 steps=[('Configure only the request budget', ['CONFIG retains its existing endpoint, model, and key fields. A retry count of zero disables automatic retries, not the initial request.'], [('Retry budget', 'The complete configuration file also contains the options introduced by the basic chapters.')]),
 ('Represent failures as structured data', ['RequestFailure holds category, status, and retry_after. Only the explicit HTTP allowlist is retryable; an unknown outcome does not enter that branch.'], [('Structured request failures', 'Error messages exclude keys, request bodies, and raw server error bodies.')]),
 ('Control one operation with a small loop', ['operation represents one HTTP attempt. run_with_retry controls attempts, waiting, and stopping; it knows no tool names and performs no file operations.', 'Waiting and randomness can be injected so regression checks do not need real delays. Exceptions other than RequestFailure propagate.'], [('Bounded retries and backoff', 'The success branch checks Signal too, discarding results that arrive after cancellation.')]),
 ('Classify HTTP failures and validate the response', ['Catch HTTPError before its parent URLError so the status is preserved. once validates the body rather than interpreting HTTP 200 as permission to execute. finish_reason must be stop or tool_calls and agree with whether tool calls are present.'], [('The scope of one request', 'The retry helper receives once, not agent_loop.'), ('Validate a complete response', 'This checks call IDs and JSON objects. Tool handlers still validate path, content, and other business fields.')]),
 ('Connect the caller and count the operations', ['call_llm retains the request body and replaces only the sending step. Retries reuse the Request without rebuilding the loop or completed tool results.', 'The regression returns 503, a tool intention, 429, and a final answer in order, counting sends, tool executions, and history entries separately.'], [('Connecting the transport layer', 'Request construction and tool execution belong to different functions.'), ('Four requests, one tool execution', 'This offline control-flow check runs through the real agent loop.')])],
 run=['Offline regression; no real model requests'],
 checks=['The initial send plus retries stays within budget; 401, unknown outcomes, and invalid responses are not retried automatically.', 'Even when requests before and after a tool encounter retryable failures, the tool executes once.', 'Cancellation stops further backoff attempts and prevents late responses from entering the loop.'],
 boundary='This policy does not guarantee that the server processes or bills a request once, and it does not recover crashed tools. Global cost budgets and cross-request deadlines are not implemented.'),

9: dict(title='Execution records and recovery', subtitle='Distinguish three kinds of missing result',
 goal='Record tool phases alongside conversation history so recovery can distinguish unstarted calls, unknown outcomes, and saved results without blindly replaying tools.',
 problems=['write can change report.txt before the process exits without saving a tool message. An intention in chat history cannot prove that the file was untouched.', 'A missing result might mean no execution or merely no durable record. Recovery needs phase evidence.'],
 theory='''<p>A basic Session records what the conversation said. An execution journal also records how far a tool progressed. These grow together during normal operation, but a process exit can leave different gaps.</p>
<h4 class="mini">What intention, start, and result establish</h4>
<pre class="ascii">assistant: call_A requests write
started: call_A is about to execute
actual tool operation
result: the return value of call_A
tool: deliver that result to the model</pre>
<p>The assistant record contains the full intention; started is persisted before the operation. The process might exit immediately afterward, or it might change the file and exit before result. Both can leave an unknown outcome. <strong>started is not a completion marker.</strong></p>
<p>result retains the return value, while the tool message supports the next model request. Keeping them separate allows recovery to complete the message pair without touching the file again. Each record passes through write, flush, and fsync, but the journal and file change remain separate operations.</p>
<h4 class="mini">Three recovery branches</h4>
<div class="tw"><table class="plain"><tr><th>Journal state</th><th>Evidence</th><th>Recovery</th></tr><tr><td>Intention only</td><td>No recorded execution start</td><td>Append a not-executed result; do not replay</td></tr><tr><td>started, no result</td><td>The operation may have happened</td><td>Block continuation with an unknown outcome</td></tr><tr><td>result, no tool message</td><td>The return value is saved</td><td>Reuse it to complete the pair</td></tr></table></div>
<p>Preserving uncertainty adds manual inspection, but avoids guessing that an operation probably did not happen. --acknowledge-unknown allows continuation after the operator has inspected the state. It certifies neither success nor failure and does not replay the old call.</p>
<h4 class="mini">The journal can fail too</h4>
<p>Journal writes use write, flush, and fsync without forming a transaction with the target file. Corrupt records cannot be skipped: losing started changes execution interpretation, and losing result discards recovery evidence. A single-writer lock covers restoration and execution; the lock file remaining on disk does not mean a writer is still alive.</p>''',
 choices=['Use a separate Journal format instead of treating a basic Session as an execution log.', 'Tool errors can become result text. Persistence failures must stop execution rather than masquerading as ordinary tool errors.', 'Restore message pairs without replaying tools; uncertain file state requires human inspection.'],
 files=['Events, strict reads, locking, and restoration', 'Connect phase records to actual tool execution'],
 steps=[('Persist evidence before proceeding', ['append saves one event with a kind field. Its caller proceeds only after it succeeds; persistence failures are not silently ignored.'], [('Append an execution event', 'fsync reduces the durability window without promising power-loss atomicity.')]),
 ('Connect started and result around the tool', ['Runtime._tool saves started, calls submit, and saves result after obtaining a string. Its try block covers the tool itself, leaving journal and host-event failures outside.'], [('Record order around real execution', 'An exit between started and result preserves an unknown outcome.')]),
 ('Restore pending calls by ID', ['restore tracks pending, starts, and results. A saved result can complete a message. Unknown outcomes raise RecoveryRequired before any model request.'], [('Restore pairing without replay', 'The restorer has no path that invokes a handler.')]),
 ('Check files and history across a real exit', ['A subprocess writes a file and exits before result. The new runner must block its Provider; another exit point after result verifies reuse of saved output.'], [('Recovery after a write and exit', 'The file state and execution count are evidence alongside the reported error.')])],
 run=['Offline recovery and locking checks', 'Read-only phase inspection of an existing advanced journal'],
 checks=['An exit after a write but before result leaves a changed file and blocks continuation without repeating the tool.', 'An exit after result restores the tool message using saved output.', 'Corruption preserves the original log; a second process cannot write the same journal concurrently.'],
 boundary='No transactions for arbitrary external actions, exactly-once execution, automatic state inspection, or basic Session migration are provided. Locking targets local filesystems.'),

10: dict(title='Streaming responses', subtitle='Preview text early; execute only a complete intention',
 goal='Collect streaming fragments into a complete response for the existing loop. Preview text neither triggers tools nor establishes a history commit.',
 problems=['The screen says that a change is coming, but arguments contains only {"path":. An incomplete input cannot reach file tools.', 'Text already displayed after a disconnect may remain visible. Whether that assistant response belongs in committed history is a separate decision.'],
 theory='''<p>A non-streaming request returns a complete message. A streaming request returns deltas, and both text and tool arguments can be fragmented. Displaying a fragment is harmless to files; executing an incomplete intention is not.</p>
<h4 class="mini">A delta is not a tool invocation</h4>
<pre class="ascii">arguments fragment 1: {"path":
arguments fragment 2: "report.txt","content":"hello"}
assembled: {"path":"report.txt","content":"hello"}</pre>
<p>The first fragment is neither complete JSON nor a complete path. The collector buffers arguments while text_delta can feed the display. Only a fully validated response returns to agent_loop, which then saves the assistant and executes tools.</p>
<h4 class="mini">Bytes, events, deltas, and messages</h4>
<p>SSE ends an event with a blank line. Its data lines form a payload whose JSON describes a delta. One readline is therefore not one assistant message. Tool fragments are grouped by index; call IDs later pair intentions with tool results. These identifiers serve different purposes.</p>
<h4 class="mini">The acceptance threshold</h4>
<p>The collector requires both an accepted finish_reason and [DONE], contiguous tool indices, valid arguments, and shared response validation. Truncation, filtering, disconnection, invalid arguments, or cancellation produce no committable response. Commit here means accepting a complete message into history, not a filesystem or database transaction.</p>
<p>A failed stream retains the recorded user input and discards the incomplete assistant. Displayed preview cannot prove that a complete message was accepted. Automatic retries might generate different content, so this path does not join a second generation onto the first.</p>''',
 choices=['Return the same response shape as call_llm so the loop does not consume deltas.', 'Keep preview text separate from argument buffers; incomplete intentions do not enter Journal.', 'Implement a single-choice Chat Completions SSE subset without assuming that other APIs share its event protocol.'],
 files=['HTTP streaming, SSE collection, and complete-message validation'],
 steps=[('Connect HTTP to the collector', ['call_stream sets stream=true, checks Content-Type, and feeds bounded readline output into collect_stream. This path has no automatic retries.'], [('The actual streaming request', 'A local SSE check exercises this connection rather than supplying only a fabricated list.')]),
 ('Assemble fields by call index', ['consume accumulates id, name, and arguments for each index. Arguments remain strings until final parsing; no submit call occurs between fragments.'], [('Consume one SSE event', 'Text deltas go to on_delta, which is a display callback, not an execution entry point.')]),
 ('Return only after complete termination', ['collect_stream frames events at blank lines and bounds total and per-line input. At termination it checks finish, DONE, and indices before constructing the message.'], [('Event boundaries and acceptance', 'Missing DONE or finish never returns a partial response.')]),
 ('Reuse the loop for persistence and execution', ['Runtime._request forwards text_delta only to display. The complete result still passes Signal and response checks before the original loop saves and executes it.'], [('Connect preview and committed responses', 'Every request source passes through response validation.')])],
 run=['Offline and local SSE checks'],
 checks=['Unicode text and fragmented arguments assemble into a complete message.', 'Missing termination, truncation, or cancellation creates no assistant history or tool side effects.', 'The local HTTP endpoint receives stream=true and returns SSE.'],
 boundary='Compatibility with all providers is unverified. Stream resumption, multimodal input, and immediate cross-thread cancellation of blocking reads are not implemented.'),

11: dict(title='Tool permissions and stopping', subtitle='The model proposes; the host authorizes',
 goal='Give each runner its own root, tool registry, and current write permission. Enforce authorization at execution time rather than relying on model compliance.',
 problems=['Removing write from tool declarations cannot guarantee that a Provider never returns write. The host must still reject unauthorized calls.', 'A path spelled inside the workspace may resolve outside it. A cancellation flag also cannot reverse an existing write.'],
 theory='''<p>A tool has a declaration for the model and a handler in the host. The declaration helps select an action; authorization decides whether that action can happen. Read-only mode must affect both.</p>
<h4 class="mini">Undeclared does not mean impossible to request</h4>
<pre class="ascii">Only read is declared
Provider still returns write("report.txt", "hello")
submit sees allow_write=False → PermissionError
report.txt must not appear</pre>
<p>The execution entry checks name, authorization, and the argument object. Handlers then check business fields. Structurally valid JSON can still contain an escaping path or an invalid content type.</p>
<h4 class="mini">The workspace belongs to the instance</h4>
<p>Shared global cwd lets one session change another session's relative-path meaning. WorkspaceTools holds an absolute root and resolves every path from it without os.chdir. Write permission comes from the current --allow-write, not from a previous journal.</p>
<h4 class="mini">Validate the resolved location</h4>
<p>Text prefixes cannot distinguish project from project-other or detect an external symlink or junction. path resolves first, then checks ancestry. Journals, Git metadata, device names, and alternate data streams have additional restrictions.</p>
<h4 class="mini">Cancellation is not rollback</h4>
<p>Signal blocks subsequent actions at cooperative checkpoints. A check just before writing reduces the cancellation window but cannot undo an operation already underway. A process exit can still leave Journal with an unknown outcome.</p>''',
 choices=['Keep tool state local to an instance without changing the global registry or cwd.', 'Use serial advanced tools without importing the basic concurrent queue or shell.', 'Treat path checks as guardrails in a trusted local host, not as an OS sandbox for Python plugins.'],
 files=['Instance roots, trusted registration, authorization, and file tools'],
 steps=[('Create session-local tool state', ['__init__ stores root, allow_write, and registry, registering only read, write, and edit. A model response cannot import or register Python functions.'], [('Instance-local tools', 'Read-only mode does not remove write handlers; the entry point still enforces authorization.')]),
 ('Authorize before invoking handlers', ['submit checks cancellation and registration before write permission. It rejects a direct write request even when no write declaration was exposed.'], [('Authorization at dispatch', 'Rejection must precede any actual file operation.')]),
 ('Resolve paths and preserve evidence', ['path does not trust a string prefix. It rejects resolved escapes and prevents tools from rewriting their journals or Git metadata.'], [('The path boundary', 'This does not isolate arbitrary trusted Python or all hostile concurrent link replacements.')]),
 ('Check cancellation before changing state', ['_write validates the path and content, checks Signal, then creates parents and writes. That checkpoint is not a transaction and cannot reverse a write already started.'], [('Business input and the write checkpoint', 'Authorization, input validity, and stopping answer different questions.')])],
 run=['Offline authorization, junction, and cancellation checks'],
 checks=['Fabricated write and bash calls are rejected without file artifacts.', 'Resolved escapes, reserved directories, and special Windows paths are rejected.', 'An already-triggered cancellation prevents file creation.'],
 boundary='No OS sandbox, authorized shell execution, Windows process-tree termination, or file rollback is provided. Trusted handlers retain host Python privileges.'),

12: dict(title='Testing and outcome evaluation', subtitle='Separate a working execution path from model success',
 goal='Define artifact criteria before running tasks, drive the real Runtime with offline scripts, and observe control flow, completion state, and files separately.',
 problems=['A successful write does not establish correct content. A final claim of completion cannot replace inspecting the actual file.', 'A scripted Provider preselects actions, so its pass rate is not a real model task-success rate.'],
 theory='''<p>Execution correctness and task effectiveness are different layers. Fault regression checks how code handles given inputs; task evaluation also asks whether the model understands a request, selects useful actions, and produces a valid result.</p>
<h4 class="mini">Start with directly checkable task contracts</h4>
<div class="tw"><table class="plain"><tr><th>Task</th><th>Scripted action</th><th>Criterion</th></tr><tr><td>read</td><td>Read line two</td><td>Exactly 2: beta\\n</td></tr><tr><td>write_edit</td><td>Write, then edit</td><td>File contains hello world</td></tr><tr><td>deny_write</td><td>Request write in read-only mode</td><td>No file and a PermissionError result</td></tr></table></div>
<p>All three use the real Runtime, Journal, and file tools. Only the intention-producing Provider is scripted, preserving the engineering connections while removing model randomness.</p>
<h4 class="mini">Each attempt needs a clean start</h4>
<p>Each attempt creates a temporary directory, input, journal, Signal, and request counter. Otherwise a correct file from one attempt could make a later non-execution pass. Three repetitions of three tasks give nine attempts: repetition checks state and control flow, not model-generation variance.</p>
<h4 class="mini">Interpret report fields separately</h4>
<p>Requests count Provider calls; tools count tool_start events, including a rejected write attempt. Duration describes runtime cost. Unmeasured tokens and monetary cost stay null; missing measurement does not mean zero cost.</p>
<p>Final criteria inspect artifacts independently. A 9/9 scripted pass means nine predefined action sequences satisfied their contracts, not 100% model success. Real comparisons also require fixed model conditions, task versions, budgets, and repeated runs.</p>''',
 choices=['Use deterministic Providers to locate engineering regressions without mixing in model changes.', 'Grade actual files and error results rather than trusting completion claims.', 'Keep per-attempt results and nonzero failure exits instead of returning only one aggregate percentage.'],
 files=['Three tasks, independent attempts, and JSON reporting', 'Fault and permission integration regression'],
 steps=[('Fix intentions while preserving real execution', ['provider returns read, write, or edit according to request count. deny_write deliberately attempts an unauthorized action to check the host, not model self-restraint.'], [('A deterministic Provider', 'This is a scripted branch sequence, not a capability model.')]),
 ('Isolate attempts and inspect artifacts', ['evaluate creates a temporary workspace and Journal for each attempt, then checks files, errors, and final content. Temporary state never becomes the next attempt\'s starting point.'], [('From one task to repeated attempts', 'The passed condition is separate from the Provider claiming success.')]),
 ('Persist a checkable report', ['main accepts repetition count and output path. Any failure produces a nonzero exit; a JSON file existing does not establish an all-pass run.'], [('Reports and process status', 'Use a fresh output path when saving evidence to preserve earlier records.')]),
 ('Check that the evaluator recognizes runtime errors', ['The test forces Runtime.run to fail, verifying that attempts remain failed records rather than disappearing or counting as passes, without exposing raw exception details.'], [('Regression for evaluator failures', 'The measurement apparatus needs its own failure criteria.')])],
 run=['Three tasks repeated three times; no real model requests'],
 checks=['read, write_edit, and deny_write use independent starts and actual result criteria.', 'real_model=false; unmeasured tokens and cost are null.', 'Runtime errors do not pass, and any failed task makes the command exit nonzero.'],
 boundary='There is no real-model baseline, cost collection, statistical-significance claim, or general coding benchmark. This small suite covers only the stated execution paths.'),

13: dict(title='Session composition and extension interfaces', subtitle='Connect the lifecycle to one loop',
 goal='Compose Provider, Journal, tools, and Signal through explicit interfaces, keeping state per session while leaving turns and message pairing in the agent loop.',
 problems=['Components working separately does not establish that the real entry point connects recovery, authorization, and stopping.', 'Shared cwd, registries, or Signal can let one session affect another.'],
 theory='''<p>agent_loop still requests responses, saves assistants, runs tools, and pairs results. Runtime does not create a second loop. It supplies the objects used at these points, tying them to one execution.</p>
<h4 class="mini">Three connection points</h4>
<div class="tw"><table class="plain"><tr><th>Interface</th><th>Loop position</th><th>Runtime responsibility</th></tr><tr><td>request_fn</td><td>Obtain a complete response</td><td>Provider or transport, plus validation</td></tr><tr><td>on_append</td><td>Append a message</td><td>Persist a Journal event</td></tr><tr><td>tool_runner</td><td>Execute an intention</td><td>started, authorization, tool, result</td></tr></table></div>
<p>Basic callers retain defaults when these new interfaces are omitted. The advanced entry point supplies its own objects rather than copying the loop to add SSE or journaling.</p>
<h4 class="mini">Which session owns the state</h4>
<pre class="ascii">Runtime A → Journal A / root A / Signal A / registry A
Runtime B → Journal B / root B / Signal B / registry B</pre>
<p>Stopping or registering a tool in A does not change B. Two runners using one journal are constrained by a single-writer lock. This is instance-state separation, not multi-agent routing or shared-file conflict resolution.</p>
<h4 class="mini">Composition also specifies what is absent</h4>
<p>The current entry does not integrate basic Session migration, shell, the concurrent queue, automatic compaction, or dynamic skills. A fixed system message and complete history are current choices. Compaction in particular must reconcile history rewriting with execution evidence; simply enabling an old hook is not complete integration.</p>''',
 choices=['One Runtime owns session objects without changing global cwd.', 'Providers are replaceable, but response validation and tool authorization remain mandatory.', 'Plugins are registered trusted host functions, not model-driven imports or remote code downloads.'],
 files=['Optional request_fn and tool_runner interfaces', 'Arguments, entry-point state, and exit codes'],
 steps=[('Construct objects for the same run', ['Runtime reads root from the Journal header and creates its own tools and Signal. Permission comes from this invocation\'s allow_write, not the log.'], [('State owned by the runner', 'The journal path is added to the tools\' reserved locations.')]),
 ('Put restoration and execution in one lifecycle', ['run restores under a lock, records new input, and enters the loop. An already completed log without a new task returns directly without another model request.'], [('Restoration, append, and loop composition', 'The lock spans restoration and execution, not merely opening the journal.')]),
 ('State the trusted extension contract', ['Name, Schema, and handler must agree. mutating defaults to True, treating an action as modifying state. The host supplies this classification; it is not inferred from the function.'], [('Trusted extension registration', 'Trusted Python retains host privileges; a registry is not a sandbox.')]),
 ('Enter the same composition from the CLI', ['--status reads saved phases and returns without a model request. Real runs create Runtime and interpret termination state. Resume uses the recorded root and current startup authorization.'], [('The advanced entry point and exit codes', 'Normal completion is 0, unfinished execution 2, runtime errors 1, and interruption 130. Exit 0 does not replace artifact validation.')])],
 run=['Entry-point help and composition regression; no real model requests'],
 checks=['Separate instances keep independent roots, registries, and Signal.', 'Repeated Provider tool IDs are rejected before a second execution.', 'Resuming a completed log sends no request; incomplete and error states retain distinct exits.'],
 boundary='Session branches, multi-agent collaboration, remote plugins, and complete Provider configuration management are absent. Registration does not establish the trustworthiness of extension code.'),
}
