import { test, expect, mock } from 'claude-code/testing'

// The bridge, faked beneath the mod: answers by path, records each request.
function fakeBridge(on: any, answers: Record<string, unknown>, seen: any[]) {
  on('http.fetch', ($: any, e: any) => {
    const path = new URL(e.url).pathname
    const body = e.init?.body ? JSON.parse(e.init.body) : undefined
    seen.push({ path, body, socketPath: e.init?.socketPath })
    const raw = answers[path]
    const answer = typeof raw === 'function' ? (raw as any)(body) : raw
    if (answer === undefined || answer === null) return { value: { status: 503, ok: false, headers: {}, text: '{}' } }
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(answer) } }
  })
}

function fakeSession(on: any) {
  mock.env(on, { HOME: '/home/t', CLAUDE_CODE_ENTRYPOINT: 'claude-desktop' })
  mock.clock(on, { now: 1_000 })
  on('session.id', () => ({ value: 'S1' }))
  on('session.cwd', () => ({ value: '/work/AI_Assistant' }))
  on('session.model', () => ({ value: 'claude-opus-5-5' }))
  on('session.version', () => ({ value: '2.1.293' }))
  on('session.start', ($: any, e: any) => ({ cwd: e.cwd }))
  on('ui.status', () => ({ value: undefined }))
  // Claude Code's own permission dialog, as the engine would ask it: no decision.
  on('classic.PermissionRequest', () => ({}))
  on('session.usage', () => ({ value: { startedAt: 0, context: { tokens: 1000, window: 200000, percent: 1 }, rateLimits: [] } }))
}

async function start($: any) {
  await $.session.start({ cwd: '/work/AI_Assistant', surface: null, isInteractive: false })
}

test('a permission request is decided on the knob', async ($, on) => {
  const seen: any[] = []
  fakeSession(on)
  fakeBridge(on, { '/v1/ask': { decision: { behavior: 'allow' } }, '/v1/state': { ok: true } }, seen)
  await start($)
  const r: any = await $.classic.PermissionRequest({
    tool_name: 'Bash', tool_input: { command: 'git push' }, permission_suggestions: [],
  } as any)
  expect(r.decision).toEqual({ behavior: 'allow' })
  const ask = seen.find(s => s.path === '/v1/ask')
  expect(ask.body).toEqual({ sid: 'S1', what: 'permission', tool: 'Bash', input: { command: 'git push' }, suggestions: [] })
  expect(ask.socketPath).toBe('/home/t/.config/ai-knob/mod.sock')
})

test('without the bridge the normal dialog decides', async ($, on) => {
  const seen: any[] = []
  fakeSession(on)
  fakeBridge(on, {}, seen)
  await start($)
  const r: any = await $.classic.PermissionRequest({ tool_name: 'Bash', tool_input: { command: 'ls' } } as any)
  expect(r.decision).toBe(undefined)
})

test('a knob that leaves it to the computer does not decide either', async ($, on) => {
  fakeSession(on)
  fakeBridge(on, { '/v1/ask': { decision: null } }, [])
  await start($)
  const r: any = await $.classic.PermissionRequest({ tool_name: 'Write', tool_input: { file_path: 'a' } } as any)
  expect(r.decision).toBe(undefined)
})

test('a question answered on the knob reaches the tool as answers', async ($, on) => {
  fakeSession(on)
  fakeBridge(on, { '/v1/ask': { answers: { 'Which?': 'B' } } }, [])
  let ran: any
  on('tool.call', ($: any, e: any) => { ran = e; return { result: { answers: e.answers } } })
  await start($)
  const questions = [{ question: 'Which?', header: 'H', options: [{ label: 'A' }, { label: 'B' }], multiSelect: false }]
  await $.tool.call({ tool: 'AskUserQuestion', questions } as any)
  expect(ran.answers).toEqual({ 'Which?': 'B' })
  expect(ran.questions).toEqual(questions)
})

test('a plan rejected on the knob is denied', async ($, on) => {
  fakeSession(on)
  fakeBridge(on, { '/v1/ask': { verdict: false } }, [])
  let ran = false
  on('tool.call', () => { ran = true; return { result: {} } })
  await start($)
  const r: any = await $.tool.call({ tool: 'ExitPlanMode', plan: '# Do it' } as any)
  expect(ran).toBe(false)
  expect(JSON.stringify(r)).toContain('Plan am Knob abgelehnt.')
})

test('knob commands run as slash commands and report back', async ($, on) => {
  const seen: any[] = []
  const commands: any[] = []
  fakeSession(on)
  let polls = 0
  fakeBridge(on, {
    '/v1/hello': { ok: true }, '/v1/state': { ok: true }, '/v1/result': { ok: true },
    // one batch of commands, then the bridge goes away (the loop sleeps on the mocked clock)
    '/v1/commands': () => (polls++ === 0 ? { commands: [{ t: 'set', key: 'effort', value: 'high' }] } : null),
  }, seen)
  on('command.run', ($: any, e: any) => { commands.push(e); return { text: '' } })
  await start($)
  for (let i = 0; i < 200 && !seen.some(s => s.path === '/v1/result'); i++) await new Promise(r => setTimeout(r, 5))
  expect(commands[0].command).toBe('effort')
  expect(commands[0].args).toBe('high')
  const result = seen.find(s => s.path === '/v1/result')
  expect(result.body.ok).toBe(true)
})

test('a mode from the knob goes through the app session tool', async ($, on) => {
  const seen: any[] = []
  const mcp: any[] = []
  fakeSession(on)
  let polls = 0
  fakeBridge(on, {
    '/v1/hello': { ok: true }, '/v1/state': { ok: true }, '/v1/result': { ok: true },
    '/v1/commands': () => (polls++ === 0 ? { commands: [{ t: 'set', key: 'mode', value: 'default' }] } : null),
  }, seen)
  on('mcp.call', ($: any, e: any) => { mcp.push(e); return { value: { content: [{ type: 'text', text: 'ok' }], isError: false } } })
  await start($)
  for (let i = 0; i < 200 && !seen.some(s => s.path === '/v1/result'); i++) await new Promise(r => setTimeout(r, 5))
  expect(JSON.stringify(mcp[0])).toContain('set_session_permission_mode')
  expect(JSON.stringify(mcp[0])).toContain('"self"')
  expect(seen.find(s => s.path === '/v1/result').body.ok).toBe(true)
})

test('a prompt typed in the session tells the bridge to follow it', async ($, on) => {
  const seen: any[] = []
  fakeSession(on)
  fakeBridge(on, { '/v1/state': { ok: true } }, seen)
  on('classic.UserPromptSubmit', () => ({}))
  await start($)
  await $.classic.UserPromptSubmit({ prompt: 'hi', permission_mode: 'default' } as any)
  for (let i = 0; i < 200 && !seen.some(s => s.body?.prompted === true); i++) await new Promise(r => setTimeout(r, 5))
  expect(seen.some(s => s.path === '/v1/state' && s.body.prompted === true)).toBe(true)
})

test('a voice prompt from the knob goes in as the person\'s own words', async ($, on) => {
  const seen: any[] = []
  const prompts: any[] = []
  fakeSession(on)
  let polls = 0
  fakeBridge(on, {
    '/v1/hello': { ok: true }, '/v1/state': { ok: true }, '/v1/result': { ok: true },
    '/v1/commands': () => (polls++ === 0 ? { commands: [{ t: 'prompt', text: 'Lass die Tests laufen' }] } : null),
  }, seen)
  on('prompt.submit', ($: any, e: any) => { prompts.push(e); return { drop: 'test' } })
  await start($)
  for (let i = 0; i < 200 && !seen.some(s => s.path === '/v1/result'); i++) await new Promise(r => setTimeout(r, 5))
  expect(prompts[0].text).toBe('Lass die Tests laufen')
  // no "The ai-knob plugin sent a message" frame around it
  expect(prompts[0].origin.asUser).toBe(true)
})

test('a voice text to edit goes into the prompt box, after what is typed', async ($, on) => {
  const seen: any[] = []
  const fills: any[] = []
  fakeSession(on)
  let polls = 0
  fakeBridge(on, {
    '/v1/hello': { ok: true }, '/v1/state': { ok: true }, '/v1/result': { ok: true },
    '/v1/commands': () => (polls++ === 0 ? { commands: [{ t: 'fill', text: 'Lass die Tests laufen' }] } : null),
  }, seen)
  on('prompt.read', () => ({ value: { text: 'Bitte:', cursor: 6 } }))
  on('prompt.fill', ($: any, e: any) => { fills.push(e); return { isFilled: true } })
  await start($)
  for (let i = 0; i < 200 && !seen.some(s => s.path === '/v1/result'); i++) await new Promise(r => setTimeout(r, 5))
  expect(fills[0].text).toBe(' Lass die Tests laufen')
  expect(fills[0].mode).toBe('append')
  expect(seen.find(s => s.path === '/v1/result').body.ok).toBe(true)
})

test('effort in the desktop app: box emptied, keystrokes asked for, draft put back', async ($, on) => {
  const seen: any[] = []
  const fills: any[] = []
  fakeSession(on)
  let polls = 0
  fakeBridge(on, {
    '/v1/hello': { ok: true }, '/v1/state': { ok: true }, '/v1/result': { ok: true },
    '/v1/type': () => ({ ok: fills.length === 1 && fills[0].text === '' }),
    '/v1/commands': () => (polls++ === 0 ? { commands: [{ t: 'app_set', key: 'effort', value: 'high' }] } : null),
  }, seen)
  on('prompt.read', () => ({ value: { text: 'Entwurf', cursor: 7 } }))
  on('prompt.fill', ($: any, e: any) => { fills.push(e); return { isFilled: true } })
  await start($)
  for (let i = 0; i < 200 && !seen.some(s => s.path === '/v1/result'); i++) await new Promise(r => setTimeout(r, 5))
  expect(seen.find(s => s.path === '/v1/type').body.cmd.value).toBe('high')
  expect(fills.map(f => f.text)).toEqual(['', 'Entwurf'])
  expect(seen.find(s => s.path === '/v1/result').body.ok).toBe(true)
})

test('effort in the desktop app: no box, no keystrokes', async ($, on) => {
  const seen: any[] = []
  fakeSession(on)
  let polls = 0
  fakeBridge(on, {
    '/v1/hello': { ok: true }, '/v1/state': { ok: true }, '/v1/result': { ok: true }, '/v1/type': { ok: true },
    '/v1/commands': () => (polls++ === 0 ? { commands: [{ t: 'app_set', key: 'effort', value: 'high' }] } : null),
  }, seen)
  on('prompt.read', () => ({ value: { text: '', cursor: 0 } }))
  on('prompt.fill', () => ({ isFilled: false }))
  await start($)
  for (let i = 0; i < 200 && !seen.some(s => s.path === '/v1/result'); i++) await new Promise(r => setTimeout(r, 5))
  expect(seen.some(s => s.path === '/v1/type')).toBe(false)
  expect(seen.find(s => s.path === '/v1/result').body.ok).toBe(false)
})
