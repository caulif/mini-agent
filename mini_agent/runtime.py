import json

from . import agent, llm
from .control import Signal
from .journal import Journal, RecoveryRequired
from .permissions import WorkspaceTools
from .streaming import call_stream
from .transport import validate_response


class Runtime:
    def __init__(self, journal, *, provider=None, allow_write=False, stream=False, emit=None):
        self.journal = journal
        header = journal.read()[0]
        self.tools = WorkspaceTools(header['root'], allow_write=allow_write)
        self.tools.reserved.update({journal.path, journal.path.with_name(journal.path.name + '.lock')})

        self.signal = Signal()
        self.provider = provider
        self.stream = stream
        self.emit = emit or (lambda kind, **kw: None)

    def _request(self, messages, tools, *, system, signal):
        if self.provider:
            response = self.provider(messages, tools, system=system, signal=signal)
        elif self.stream:
            response = call_stream(messages, tools, system=system, signal=signal,
                                   on_delta=lambda text: self.emit('text_delta', text=text))
        else:
            response = llm.call_llm(messages, tools, system=system, signal=signal)
        signal.check()
        response = validate_response(response)
        used = {c['id'] for m in messages for c in m.get('tool_calls') or []}
        if any(c['id'] in used for c in llm.extract_tool_calls(response)):
            raise RecoveryRequired('Provider reused a tool ID; execution refused')
        return response

    def _tool(self, call, *, signal, emit):
        key, fn = call['id'], call['function']
        inp = json.loads(fn['arguments'])
        signal.check()
        self.journal.append('started', id=key)

        emit('tool_start', name=fn['name'], input=inp)
        try:
            output = self.tools.submit(fn['name'], inp, signal=signal)
        except Exception as error:
            output = f'Error: {type(error).__name__}'
        if not isinstance(output, str):
            raise TypeError('Tool must return a string; outcome remains unknown')
        self.journal.append('result', id=key, output=output)
        emit('tool_end', name=fn['name'], output=output)
        return {'tool_call_id': key, 'content': output}

    def run(self, text=None, *, max_turns=8, acknowledge_unknown=False):
        if type(max_turns) is not int or max_turns < 1:
            raise ValueError('max_turns must be a positive integer')
        with self.journal.locked():
            self.signal.check()
            messages = self.journal.restore(acknowledge_unknown=acknowledge_unknown)
            if text is not None:
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('Task must not be empty')
                msg = {'role': 'user', 'content': text}
                self.journal.append('message', message=msg)
                messages.append(msg)
            if not messages:
                raise ValueError('A new session requires a task')

            if text is None and messages[-1]['role'] == 'assistant' and not messages[-1].get('tool_calls'):
                return messages
            return agent.agent_loop(messages, tools=self.tools.definitions(), signal=self.signal,
                system='You are a coding agent. Use only the provided tools. '
                       'Tool outcomes marked unknown require human inspection; never replay them.',
                max_turns=max_turns, emit=self.emit, request_fn=self._request, tool_runner=self._tool,
                on_append=lambda msg: self.journal.append('message', message=msg))
