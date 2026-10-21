import http.client
import json
import urllib.error
import urllib.request

from .config import CONFIG
from .llm import SYSTEM_PROMPT
from .retry import RequestFailure
from .transport import validate_response


def collect_stream(lines, *, signal, on_delta=None):
    content, calls, finish, done, size = [], {}, None, False, 0
    event = []

    def consume(payload):
        nonlocal finish, done
        if payload == '[DONE]':
            done = True
            return
        data = json.loads(payload)
        choices = data['choices']
        if not choices:
            return
        if len(choices) != 1 or choices[0].get('index') != 0 or finish is not None:
            raise ValueError('Expected one choice and no deltas after finish')
        choice = choices[0]
        delta = choice.get('delta', {})
        if delta.get('role', 'assistant') != 'assistant':
            raise ValueError('Invalid role')
        text = delta.get('content')
        if text is not None:
            if not isinstance(text, str):
                raise ValueError('Text delta must be a string')
            content.append(text)
            if on_delta:
                on_delta(text)
        for fragment in delta.get('tool_calls') or []:
            index = fragment['index']
            if type(index) is not int or not 0 <= index < 32:
                raise ValueError('Tool index out of range')
            call = calls.setdefault(index, {'id': '', 'type': 'function',
                                            'function': {'name': '', 'arguments': ''}})
            if fragment.get('type', 'function') != 'function':
                raise ValueError('Unsupported tool type')
            for field in ('id',):
                value = fragment.get(field, '')
                if not isinstance(value, str):
                    raise ValueError('Invalid ID delta')
                call[field] += value
            for field in ('name', 'arguments'):
                value = fragment.get('function', {}).get(field, '')
                if not isinstance(value, str):
                    raise ValueError('Invalid argument delta')
                call['function'][field] += value
        reason = choice.get('finish_reason')
        if reason is not None:
            if reason not in {'stop', 'tool_calls'}:
                raise RequestFailure('incomplete_response')
            finish = reason

    try:
        for raw in lines:
            signal.check()
            size += len(raw)
            if size > 2_000_000 or len(raw) > 262144:
                raise ValueError('Stream exceeds size limit')
            line = raw.decode('utf-8').rstrip('\r\n')
            if not line:
                if event:
                    consume('\n'.join(event))
                    event = []
                    if done:
                        break
            elif line.startswith('data:'):
                event.append(line[5:].lstrip(' '))
        signal.check()
        if not done or finish is None or sorted(calls) != list(range(len(calls))):
            raise RequestFailure('incomplete_stream')
        if (finish == 'tool_calls') != bool(calls):
            raise ValueError('finish_reason does not match tool calls')
        message = {'role': 'assistant', 'content': ''.join(content) if content else None}
        if calls:
            message['tool_calls'] = [calls[i] for i in sorted(calls)]
        return validate_response({'choices': [{'message': message, 'finish_reason': finish}]})
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise RequestFailure('invalid_stream') from None


def call_stream(messages, tools, *, system=None, signal, on_delta=None, opener=None):
    body = {'model': CONFIG['model'], 'max_tokens': CONFIG['max_tokens'], 'stream': True,
            'messages': [{'role': 'system', 'content': system or SYSTEM_PROMPT}] + messages}
    if tools:
        body.update(tools=tools, tool_choice='auto')
    request = urllib.request.Request(CONFIG['base_url'].rstrip('/') + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + CONFIG['api_key']})

    try:
        signal.check()
        with (opener or urllib.request.urlopen)(request, timeout=CONFIG['request_timeout_s']) as response:
            if 'text/event-stream' not in response.headers.get('Content-Type', ''):
                raise RequestFailure('invalid_stream')
            lines = iter(lambda: response.readline(262145), b'')
            return collect_stream(lines, signal=signal, on_delta=on_delta)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise RequestFailure('http_error', status=status) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        raise RequestFailure('outcome_unknown') from None
