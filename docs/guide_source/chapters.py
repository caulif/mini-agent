def code(path, symbol, title, note, focus=()):
    return dict(path=path, symbol=symbol, title=title, note=note, focus=focus)


def step(title, points, *snippets):
    return dict(title=title, points=points, snippets=list(snippets))


CHAPTERS = [
 dict(num=8, title='失败与重试', subtitle='只重发请求，不重做已经发生的操作',
 goal='给一次模型请求增加有限重试，同时保留已经完成的工具操作；不把整个 agent 任务从头再跑。',
 problems=['工具已经把 report.txt 写好了，下一次模型请求却返回503。此时需要重发的是请求，文件修改不会因为网络错误而撤销。',
           'HTTP 请求成功也可能收到坏 JSON 或半截工具参数。决定是否继续执行之前，还需要判断响应本身是否可用。'],
 theory='''<p>一次正常工具循环里有两种动作：<strong>模型返回下一步意图，宿主执行这个意图</strong>。两者在代码里相邻，却不能用同一种方式重试。请求可以在约定范围内再次发送；写文件一旦发生，就已经改变了外部状态。</p>
<h4 class="mini">失败的是哪一步，重试就应该留在哪一层</h4>
<p>以“修改 report.txt 后概括结果”为例，模型先返回 write，程序写入文件并保存 tool 结果，再发下一次请求。第二次请求遇到503时，历史里已经有写入结果；再次发送这份历史，并不需要回到 write。</p>
<pre class="ascii">第一次请求 → write 意图 → 文件已写入 → 保存 tool 结果
                                           ↓
                                   第二次模型请求
                                   503 → 等待 → 重发
                                           ↓
                                      最终回答</pre>
<p>写文件这类改变外部状态的动作叫作<strong>副作用</strong>。它不是“有害行为”的同义词，只说明重新运行代码可能再次改变现场。如果操作是追加记录，重复执行就可能产生两条记录；当前进阶 write 是覆盖写入，也不能因此把重跑工具当作通用恢复方式。</p>
<h4 class="mini">失败不是一个统一的类别</h4>
<p>401通常需要修正凭据，429可能需要等待，网络超时则只说明客户端没有取得可用响应。服务端可能已经处理了超时请求，所以“没有收到结果”不能解释成“没有执行”。</p>
<div class="tw"><table class="plain"><tr><th>情况</th><th>当前策略</th><th>原因</th></tr><tr><td>429、502、503、504</td><td>次数预算内重发</td><td>明确允许的状态码</td></tr><tr><td>其他HTTP错误</td><td>停止并保留状态码</td><td>不推断重复发送能修复</td></tr><tr><td>超时或断连</td><td>结果未知，不自动重发</td><td>无法确认服务端是否处理</td></tr><tr><td>坏结构或明确截断</td><td>拒绝交给工具循环</td><td>尚未得到可用意图</td></tr></table></div>
<h4 class="mini">重试次数、等待和总耗时是三件事</h4>
<p>max_retries=2 表示首发加最多两次重试，总计最多3次。退避等待从0.5秒起按指数增加，再乘0.5—1.0的随机量，避免多个客户端同时重发。Retry-After 是服务端要求的等待下限；要求30秒而本地上限10秒时，当前代码停止，不会提前发送。</p>
<p>10秒只是一次退避的上限。每次请求还有自己的超时，因此它不是总任务时限。Signal 能打断退避，也在响应返回后检查；阻塞的 urllib 读取仍不能保证即时取消。</p>''',
 choices=['重试函数只包一次HTTP操作；工具执行留在外层循环，结构上缩小重放范围。',
          '用 RequestFailure 的类别与状态码判断，不从任意异常字符串猜测是否重发。',
          '成功读取HTTP正文后仍校验响应；协议有效与业务参数有效分别处理。'],
 files=[('修改','mini_agent/config.py','集中保存次数与等待预算'),('新增','mini_agent/retry.py','错误类型、退避与停止'),('新增','mini_agent/transport.py','HTTP分类与响应校验'),('修改','mini_agent/llm.py','将发送段交给传输层'),('新增','mini_agent/retry_selfcheck.py','请求与副作用范围的回归')],
 steps=[step('配置只约束请求层', ['CONFIG 保留地址、模型与密钥等原字段，只增加重试预算。次数为0表示关闭自动重试，不会关闭正常请求。'],
    code('mini_agent/config.py','CONFIG.update','重试预算','完整配置文件仍包含基础七章的其他选项。')),
   step('把错误变成可以判断的数据', ['RequestFailure 保存 category、status 和 retry_after。只有明确的HTTP错误白名单返回 retryable=True，未知结果不会进入重发分支。'],
    code('mini_agent/retry.py','RequestFailure','结构化请求错误','错误消息不携带密钥、请求正文或服务端原始错误正文。',('return self.category',))),
   step('用一个小循环管理一次操作', ['operation 代表一次HTTP尝试。run_with_retry 负责次数、等待和停止；它不知道工具名称，也不会执行文件操作。','wait 和随机数可以注入，回归不需要真实等待。异常不是 RequestFailure 时继续向外传播。'],
    code('mini_agent/retry.py','run_with_retry','有限重试与退避','成功分支也检查Signal，避免把停止后到达的结果继续使用。',('result = operation()','except RequestFailure'))),
   step('在HTTP边界分类并检查响应', ['HTTPError 在它的父类 URLError 之前处理，以保留状态码。once 读取正文后调用 validate_response，不把HTTP 200直接解释成可执行意图。finish_reason 只接受 stop 或 tool_calls，并要求与是否存在工具调用一致。'],
    code('mini_agent/transport.py','post_json','一次请求的范围','传给重试函数的是once，不是agent_loop。',('return run_with_retry(',)),
    code('mini_agent/transport.py','validate_response','完整响应检查','这里检查工具ID和JSON对象；path与content的业务约束仍由工具处理。')),
   step('接回原调用方并核对次数', ['call_llm 保留原请求体，只替换发送段。同一个Request可以重发，主循环与已写入的工具结果不重建。','回归把503、工具意图、429、最终回答依次返回，分别统计HTTP发送、工具执行和历史条数。'],
    code('mini_agent/llm.py','call_llm','请求层的接线','请求体构造与执行工具处于不同函数。',('data = post_json',)),
    code('mini_agent/retry_selfcheck.py','TransportChecks.test_full_agent_does_not_replay_tool','四次请求，一次工具','这是经过真实主循环的离线控制流检查。'))],
 boundary='当前策略不保证服务端只处理或计费一次，也不提供工具崩溃恢复；全局费用预算与跨请求总时限未实现。'),

 dict(num=9, title='执行记录与恢复', subtitle='把“没有结果”拆成三种状态',
 goal='在聊天历史之外记录工具阶段，恢复时区分未开始、结果未知与结果已保存，不盲目重做旧调用。',
 problems=['write 改完 report.txt 后，进程可能在保存 tool 消息之前退出。聊天记录里只有意图，无法证明文件没有被改过。','一条缺失的结果消息既可能表示未执行，也可能只是没有来得及落盘；恢复必须依据执行阶段。'],
 theory='''<p>基础 Session 回答“这段对话说过什么”，执行日志还要回答“工具走到了哪一步”。这两份信息在正常运行时一起增长，进程退出时却可能留下不同缺口。</p>
<h4 class="mini">意图、开始与结果各证明什么</h4>
<pre class="ascii">assistant：call_A要求write
started：call_A准备执行
实际工具操作
result：call_A的返回值
tool：把call_A的结果交回模型</pre>
<p>assistant 保存完整调用意图，started 在操作之前落盘。它只证明准备执行：程序可能写完started就退出，也可能已经写完文件但还没保存result。因此<strong>started不是完成标记</strong>。</p>
<p>result 保存执行返回值，tool 消息用于下一次模型请求。二者分开，恢复器才能在result已保存时补消息，而不用再执行文件操作。</p>
<h4 class="mini">恢复的三种分支</h4>
<div class="tw"><table class="plain"><tr><th>日志状态</th><th>已有依据</th><th>恢复</th></tr><tr><td>只有意图</td><td>没有进入已记录的执行阶段</td><td>补未执行说明，不重放</td></tr><tr><td>started，无result</td><td>操作可能已经发生</td><td>结果未知，阻止续跑</td></tr><tr><td>result，无tool消息</td><td>返回值已经保存</td><td>复用结果补配对</td></tr></table></div>
<p>保留未知会增加一次人工核对，但比猜测“应该没执行”更可靠。--acknowledge-unknown 表示操作者允许核对后继续；它不认证旧操作成功或失败，也不会重新执行旧调用。</p>
<h4 class="mini">日志也有自己的失败方式</h4>
<p>Journal 每条记录经过write、flush、fsync，但它与实际文件不是同一个事务。日志损坏不能跳过：丢一条started会改变执行判断，丢一条result会抹去恢复依据。单写者锁覆盖恢复和运行，防止两个进程各补一次消息；锁文件存在不代表进程仍在运行。</p>''',
 choices=['独立Journal格式，不把基础Session文件自动当作执行日志。','工具错误可以变成结果文本，日志落盘失败必须中止，不能伪装成普通工具错误。','恢复只补历史配对，不自动重放旧工具；文件现场的未知由人工核对。'],
 files=[('新增','mini_agent/journal.py','事件、严格读取、文件锁与恢复'),('新增','mini_agent/runtime.py','把执行阶段接到真实工具调用')],
 steps=[step('让证据先于下一步操作落盘',['append 只负责保存一条带kind的事件。调用方在它成功返回之后才继续执行，落盘错误不会被静默忽略。'],code('mini_agent/journal.py','Journal.append','追加一条执行事件','fsync缩小未持久化窗口，不承诺掉电原子性。')),
 step('在工具前后连接started与result',['Runtime._tool 先保存started，再调用submit，拿到字符串结果后保存result。try只包工具本身，日志与宿主事件错误留在外层。'],code('mini_agent/runtime.py','Runtime._tool','实际执行与记录顺序','started与result之间的退出都保留为未知。',("self.journal.append('started'","self.journal.append('result'"))),
 step('按ID恢复尚未配齐的调用',['restore 以pending、starts和results追踪同一ID。已存result可以补消息；未知先抛RecoveryRequired，发生在模型请求之前。'],code('mini_agent/journal.py','Journal.restore','恢复配对，不执行旧工具','恢复器没有调用handler的路径。',('if unknown and not acknowledge_unknown:',))),
 step('在真实退出点检查文件与历史',['子进程先写文件，在result之前直接退出。新运行器恢复时应阻止Provider调用；另一个退出点放在result之后，验证复用结果。'],code('mini_agent/advanced_selfcheck.py','AdvancedChecks.test_real_crash_unknown_blocks_then_explicit_ack','写后退出的恢复回归','不能只依据异常文字判断，现场文件与调用次数也属于判据。'))],
 boundary='不提供任意外部动作的事务、恰好一次执行、自动现场核对或旧Session迁移；锁的适用范围为本机文件系统。'),

 dict(num=10, title='流式响应', subtitle='文字可以先显示，工具必须等完整',
 goal='把流式片段收集成原循环可以使用的完整响应；预览文字不触发工具，也不等同于历史提交。',
 problems=['界面显示了“准备修改”，arguments却只收到 {"path":。半截参数不能传给文件工具。','断流后已经显示的文字可能留在屏幕上，但这次assistant是否应进入正式历史，需要单独判定。'],
 theory='''<p>非流式请求一次返回完整消息，流式请求则不断返回delta。文字与工具参数都可能被拆开：显示一个片段很容易，执行一个不完整意图却会改变文件。</p>
<h4 class="mini">增量不是一次工具调用</h4>
<pre class="ascii">arguments片段1：{"path":
arguments片段2："report.txt","content":"hello"}
拼接结果：{"path":"report.txt","content":"hello"}</pre>
<p>第一段既不是完整JSON，也没有完整路径。收集器先缓存参数；文字可以通过text_delta单独发给界面。只有完整响应通过校验后，才返回给agent_loop，由它保存assistant并执行工具。</p>
<h4 class="mini">字节、事件、增量和消息</h4>
<p>SSE用空行结束一个事件，data行组成事件负载，负载里的JSON再描述delta。因此一次readline不等于一条assistant消息。多个工具的片段按index聚合，id则留给后续tool结果配对，二者用途不同。</p>
<h4 class="mini">完整消息的门槛</h4>
<p>当前收集器要求合法finish_reason与[DONE]均到达，工具索引连续、参数有效，再通过共享响应校验。length、content_filter、断流、坏参数与停止都不返回可提交消息。这里的提交只是“允许进入正式历史”，不是文件或数据库事务。</p>
<p>断流时保留已记录的用户输入，丢弃未完成assistant。已经展示的预览不能回头证明完整消息曾被接受；自动重试可能生成不同内容，当前流式路径因此不拼接第二次生成。</p>''',
 choices=['收集器返回与call_llm相同的响应形状，主循环不理解delta。','文本预览与工具参数缓冲分离；未完成意图不写Journal。','只实现单choice Chat Completions SSE子集，不把其他API事件格式视作相同协议。'],
 files=[('新增','mini_agent/streaming.py','真实HTTP连接、SSE聚合与完整消息校验')],
 steps=[step('把HTTP连接交给流收集器',['call_stream设置stream=true，检查Content-Type，以有界readline把网络行交给collect_stream。此路径不自动重试。'],code('mini_agent/streaming.py','call_stream','真实流式请求','本机SSE用例验证这条连接，而不只是传入一个预造列表。')),
 step('按调用索引拼接字段',['consume把同一index的id、name和arguments分别累积。参数保持字符串，最终才解析为对象；中间没有submit调用。'],code('mini_agent/streaming.py','collect_stream.consume','消费一个SSE事件','文本增量只送往on_delta，不是执行入口。')),
 step('在完整结束之后返回响应',['collect_stream按空行分帧，限制总字节数和单行长度；终止后检查finish、DONE与调用索引，再形成完整message。'],code('mini_agent/streaming.py','collect_stream','事件边界与提交门槛','缺少DONE或finish，均不返回半截响应。',('if not done or finish is None',))),
 step('复用原循环的执行与落盘顺序',['Runtime._request只把text_delta送往显示。完整结果返回后仍检查Signal与响应结构，后续保存和执行继续沿原循环进行。'],code('mini_agent/runtime.py','Runtime._request','预览与正式响应的接线','不同请求来源经过同一响应检查。'))],
 boundary='未验证所有服务商兼容性；不支持流断点续传、多模态或跨线程即时关闭阻塞读取。'),

 dict(num=11, title='工具权限与停止', subtitle='模型选择动作，宿主决定能否执行',
 goal='让根目录、工具注册与本次写授权属于运行器实例，在执行入口检查权限，而不依赖模型自律。',
 problems=['只从tools声明中删除write，不能保证Provider永远不返回write；宿主仍需拒绝未经授权的调用。','路径名称在工作区下，不代表解析后的位置仍在工作区；停止信号也不能撤销已发生的写入。'],
 theory='''<p>工具有两份表示：声明发给模型，handler留在宿主。声明帮助模型选择动作，授权则决定真实动作能否发生。默认只读需要同时影响这两个位置。</p>
<h4 class="mini">未声明不等于无法调用</h4>
<pre class="ascii">请求只提供read
Provider仍返回write("report.txt", "hello")
submit检查allow_write=False → PermissionError
report.txt不应产生</pre>
<p>执行入口检查名称、授权和参数对象，handler再检查具体业务字段。一个结构完整的JSON响应仍可能包含越界路径或错误content类型。</p>
<h4 class="mini">工作目录是实例状态</h4>
<p>两个会话如果共享全局cwd，一个切目录可能改变另一个相对路径的含义。WorkspaceTools保存自己的绝对root，每个路径从该root解析，不使用os.chdir。写权限来自本次--allow-write，不从旧日志继承。</p>
<h4 class="mini">检查的是实际路径</h4>
<p>字符串前缀无法区分project与project-other，也无法识别指向外部的符号链接或junction。path先resolve，再检查祖先关系；日志目录、Git元数据、设备名和备用数据流有额外限制。</p>
<h4 class="mini">停止不是回滚</h4>
<p>Signal在检查点阻止后续动作。写入开始之前的检查能减少停止后继续操作的窗口，但不能撤销已经开始的修改。进程在操作中退出时，Journal仍可能只能保留未知。</p>''',
 choices=['实例工具不改变全局注册表或cwd。','当前工具串行，不接 mini agent-1 的并发队列与shell。','路径检查定位于可信本地宿主，不把Python插件注册称作OS沙箱。'],
 files=[('新增','mini_agent/permissions.py','实例根目录、可信注册、授权与文件工具')],
 steps=[step('给每个会话建立自己的工具状态',['__init__保存root、allow_write与registry，只注册read、write、edit。模型不能通过响应导入或注册Python函数。'],code('mini_agent/permissions.py','WorkspaceTools.__init__','实例级工具集','默认只读并不删除写handler；入口仍有授权检查。')),
 step('在handler之前检查授权',['submit先检查停止和注册表，再判断写权限。即使声明没有暴露write，这里仍会拦截直接传入的调用。'],code('mini_agent/permissions.py','WorkspaceTools.submit','调度入口的权限判断','拒绝应发生在实际文件操作之前。',('if write and not self.allow_write:',))),
 step('解析路径并保护执行证据',['path不相信字符串前缀。resolve后的越界路径被拒绝，模型也不能通过工具修改自己的日志与Git元数据。'],code('mini_agent/permissions.py','WorkspaceTools.path','路径边界','这不防御任意恶意并发进程的链接替换或可信Python代码。')),
 step('在实际修改前检查停止',['_write先检查路径和content，再检查Signal，最后创建父目录和写入。检查点不是文件事务，不能撤回已开始的写入。'],code('mini_agent/permissions.py','WorkspaceTools._write','业务字段与修改检查点','授权、参数和停止分别承担不同判断。'))],
 boundary='没有OS沙箱、shell授权执行、Windows进程树终止或文件回滚；可信handler仍拥有宿主Python权限。'),

 dict(num=12, title='测试与效果评估', subtitle='把程序走通与模型做对分开',
 goal='预先定义任务产物判据，让真实Runtime接受离线脚本驱动，分别观察控制流、结束状态和文件结果。',
 problems=['write返回成功不代表内容正确，最终回答“完成”也不能代替读取实际文件。','脚本Provider预先规定了动作，它的通过率不能当作模型自行完成任务的成功率。'],
 theory='''<p>运行机制和任务效果是两个层次。故障回归检查给定输入下程序怎样处理；任务评测还涉及模型是否理解要求、选择有效动作，并产出符合标准的结果。</p>
<h4 class="mini">先固定能直接检查的任务契约</h4>
<div class="tw"><table class="plain"><tr><th>任务</th><th>脚本动作</th><th>判据</th></tr><tr><td>read</td><td>读取第二行</td><td>结果精确为2: beta\\n</td></tr><tr><td>write_edit</td><td>先写再编辑</td><td>文件内容为hello world</td></tr><tr><td>deny_write</td><td>只读时仍提出write</td><td>无文件且返回PermissionError</td></tr></table></div>
<p>三个任务经过真实Runtime、Journal和文件工具，只有生成意图的Provider被脚本替换。这样保留工程连接，把模型随机性移出检查范围。</p>
<h4 class="mini">一次尝试需要新的起点</h4>
<p>每个attempt创建临时目录、输入、日志、Signal和请求计数器。否则上一轮的正确文件可能让下一轮没有执行也被判通过。三任务各重复三次得到9个attempt，重复检查的是状态与控制流，不是模型生成方差。</p>
<h4 class="mini">报告中的数值各表示什么</h4>
<p>请求数来自Provider调用，工具数来自tool_start，所以被拒绝的write也可能计为一次工具尝试。耗时记录运行代价，未采集的token与费用保留null。没有测量不等于零成本。</p>
<p>最终判据独立读取产物。9/9脚本通过只能说明9次预设动作完成了约定控制流，不能报告为“模型成功率100%”。真实比较还需要固定模型、任务版本、预算和多次运行。</p>''',
 choices=['用确定性Provider定位工程回归，避免把模型变化混进机制检查。','以实际文件和错误结果评分，不信任最终文字声明。','保留逐次结果及非零失败退出码，不只输出一个汇总百分比。'],
 files=[('新增','mini_agent/evaluation.py','三任务、独立尝试与JSON报告'),('新增','mini_agent/advanced_selfcheck.py','故障与权限集成回归')],
 steps=[step('固定脚本意图而保留真实执行链',['provider按请求次数返回read、write或edit；deny_write故意发起越权调用，检查程序入口而不是模型自律。'],code('mini_agent/evaluation.py','evaluate.provider','确定性Provider','这个函数不是能力模型，动作来自预设分支。')),
 step('隔离尝试并检查实际产物',['evaluate为每次尝试创建临时目录和Journal。运行结束后直接检查文件、错误与最终内容，临时数据不影响下一轮。'],code('mini_agent/evaluation.py','evaluate','一次任务到一组尝试','passed条件与Provider宣称成功的文字分开。',('passed = not error_kind',))),
 step('把判定保存为可检查的报告',['main接受重复次数与输出路径。只要存在失败，进程返回非零；JSON文件存在不表示任务全部通过。'],code('mini_agent/evaluation.py','main','报告与退出状态','保存输出时使用新的路径，避免覆盖已有实验记录。')),
 step('检查评估器能否识别运行错误',['用例让Runtime.run确定地失败，确认报告给出失败而不是丢掉样本或误计通过，同时不泄露原始异常内容。'],code('mini_agent/advanced_selfcheck.py','AdvancedChecks.test_evaluation_records_runtime_failures','评估错误的回归','测量装置也需要自己的失败判据。'))],
 boundary='没有真实模型基线、成本采集、统计显著性结论或通用coding benchmark；小任务集只覆盖指定运行路径。'),

 dict(num=13, title='会话组合与扩展接口', subtitle='把生命周期接回同一条循环',
 goal='通过明确接口组合Provider、Journal、工具与Signal，让每个会话有自己的状态，并保留主循环的轮次和消息配对职责。',
 problems=['组件各自能工作，不代表实际入口已经接入恢复、授权和停止。','两个会话共享cwd、注册表或Signal，会让一个会话的操作影响另一个。'],
 theory='''<p>agent_loop仍负责请求、保存assistant、执行工具与配对结果。Runtime不另造一套循环，而是提供这些位置需要调用的对象，让它们属于同一次运行。</p>
<h4 class="mini">三个接线位置</h4>
<div class="tw"><table class="plain"><tr><th>接口</th><th>循环何时调用</th><th>Runtime职责</th></tr><tr><td>request_fn</td><td>获取完整响应</td><td>Provider或传输路径及校验</td></tr><tr><td>on_append</td><td>追加消息</td><td>保存Journal事件</td></tr><tr><td>tool_runner</td><td>执行工具意图</td><td>started、授权、工具、result</td></tr></table></div>
<p>基础调用方不传新增接口时仍走默认路径。进阶入口显式接入自己的对象，不因为增加SSE或执行日志而复制主循环。</p>
<h4 class="mini">状态属于哪个会话</h4>
<pre class="ascii">Runtime A → Journal A / root A / Signal A / registry A
Runtime B → Journal B / root B / Signal B / registry B</pre>
<p>A停止或注册工具不改变B。若两个运行器指向同一日志，则由单写者锁阻止并发使用。这是实例状态分离，不包含多agent的任务路由与共享文件冲突处理。</p>
<h4 class="mini">组合也要说明没有接入什么</h4>
<p>当前入口没有基础Session迁移、shell、并发队列、自动压缩或动态技能加载。固定system文本与完整历史是当前选择；尤其自动摘要涉及执行证据与消息改写的协调，不能只把旧钩子打开就算完成。</p>''',
 choices=['一个Runtime持有一次会话的对象，不改变全局cwd。','Provider可以替换，但完整响应检查和后续工具授权不随替换消失。','插件是可信宿主函数注册，不包含模型驱动import或远程代码下载。'],
 files=[('修改','mini_agent/agent.py','可选request_fn与tool_runner接口'),('新增','mini_agent/advanced.py','参数、入口状态与退出码')],
 steps=[step('构造同一次运行的对象',['Runtime从Journal头部取得root，创建自己的工具和Signal。权限来自本次allow_write，不从日志继承。'],code('mini_agent/runtime.py','Runtime.__init__','运行器持有的状态','Journal记录位置也加入工具保留路径。')),
 step('把恢复与循环放在同一生命周期',['run先持锁恢复，再记录新输入并进入循环。正常完成的日志在没有新任务时直接返回，不再次请求模型。'],code('mini_agent/runtime.py','Runtime.run','恢复、追加与主循环的组装','锁覆盖整个恢复与运行，不只覆盖打开日志的一瞬间。')),
 step('注册可信工具时明确契约',['名称、Schema与handler必须对应。mutating默认True，表示按修改类动作处理；这由宿主标注，不是自动分析函数副作用。'],code('mini_agent/permissions.py','WorkspaceTools.register','可信扩展入口','可信Python仍拥有宿主权限，注册表不是沙箱。')),
 step('从命令行进入同一组装路径',['--status读取已有阶段后返回，不请求模型。实际运行创建Runtime并解释结束状态；root来自恢复日志，写权限来自本次启动。'],code('mini_agent/advanced.py','main','进阶入口与退出码','正常完成0、未完成2、运行错误1、中断130；退出0不代替产物验收。'))],
 boundary='没有会话分支、多agent协作、远程插件或完整Provider配置管理；扩展代码的可信性不由注册接口保证。'),
]
