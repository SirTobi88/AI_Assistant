import type { Register, EngineInterface } from 'claude-code'

// AI Knob: this session <-> the bridge (bridge/mod_server.py) over a Unix
// socket. Reports state, context and limits; hands permission requests,
// questions and plans to the knob; carries out the knob's commands. Whenever
// the bridge or the knob isn't there, every hook passes the event on
// unchanged, so Claude Code behaves as without the mod.

// Session states, numbered as in docs/protocol.md.
const IDLE = 1
const THINKING = 2
const TOOL = 4
const COMPACTING = 5
const NEEDS_PERMISSION = 6
const ASKING = 7
const ERROR = 9
const DONE = 10

const HEARTBEAT_MS = 10_000
const RETRY_MS = 5_000
const POLL_WAIT_S = 25

let sock = ''
let sid = ''
let turnId: string | undefined
let state = IDLE
let mode = ''
let effort = ''
let fast = false
let title = ''
let alive = false
let promptedAt = 0   // ms, last prompt typed in this session
let bridgeUp = false

async function socketPath($: EngineInterface): Promise<string> {
  const own = await $.env.get('AI_KNOB_MOD_SOCKET')
  if (own) return own
  return `${(await $.env.get('HOME')) ?? ''}/.config/ai-knob/mod.sock`
}

async function call($: EngineInterface, method: string, path: string, body?: unknown): Promise<any> {
  if (!sock || !sid) return null
  try {
    const r = await $.http.fetch(`http://bridge${path}`, {
      method,
      socketPath: sock,
      headers: { 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    const up = r.ok
    if (up !== bridgeUp) {
      bridgeUp = up
      $.ui.status(up ? 'Knob ●' : undefined)
    }
    return r.ok ? JSON.parse(r.text) : null
  } catch {
    if (bridgeUp) {
      bridgeUp = false
      $.ui.status(undefined)
    }
    return null
  }
}

// Never throws: it runs unawaited from hooks and timers.
async function report($: EngineInterface, prompted = false): Promise<void> {
  if (!sid) return
  try {
    const usage: any = await $.session.usage().catch(() => ({}))
    await call($, 'POST', '/v1/state', {
      sid, state, mode, effort, fast, title, prompted, prompted_at: promptedAt,
      model: await $.session.model(),
      cwd: await $.session.cwd(),
      ctx_pct: usage.context?.percent ?? -1,
      tokens: usage.context?.tokens ?? -1,
      rate_limits: usage.rateLimits ?? [],
    })
  } catch {
    // the module is unloading or the session ending: nothing to report to
  }
}

function setState($: EngineInterface, next: number): void {
  if (next === state) return
  state = next
  void report($)
}

async function runCommand($: EngineInterface, cmd: any): Promise<void> {
  let ok = true
  let error: string | undefined
  try {
    if (cmd.t === 'set' && cmd.key === 'model') {
      await $.command.run({ command: 'model', args: String(cmd.value) })
    } else if (cmd.t === 'set' && cmd.key === 'effort') {
      await $.command.run({ command: 'effort', args: String(cmd.value) })
      effort = String(cmd.value)
    } else if (cmd.t === 'set' && cmd.key === 'mode') {
      // Desktop app only (the bridge sends it nowhere else): the app's own
      // session tool, which shows its approval card before a mode that does
      // more without asking.
      const r: any = await $.mcp.call('ccd_session_mgmt', 'set_session_permission_mode',
        { session_id: 'self', mode: String(cmd.value) })
      if (r?.isError) {
        ok = false
        error = (r.content ?? []).map((c: any) => c.text ?? '').join(' ') || 'refused'
      } else {
        mode = String(cmd.value)
      }
    } else if (cmd.t === 'set' && cmd.key === 'fast') {
      await $.command.run({ command: 'fast', args: cmd.value ? 'on' : 'off' })
      fast = Boolean(cmd.value)
    } else if (cmd.t === 'interrupt') {
      if (turnId) {
        await $.turn.abort({ turnId })
      } else {
        ok = false
        error = 'no turn running'
      }
    } else if (cmd.t === 'prompt' && typeof cmd.text === 'string' && cmd.text) {
      // The person's own spoken words: bare, without the plugin frame.
      await $.prompt.submit({ text: cmd.text, asUser: true })
    } else if (cmd.t === 'fill' && typeof cmd.text === 'string' && cmd.text) {
      // To edit on the computer: after whatever is already typed, not over it.
      const box = await $.prompt.read()
      const r = await $.prompt.fill({ text: box.text ? ` ${cmd.text}` : cmd.text, mode: 'append' })
      if (!r.isFilled) { ok = false; error = 'prompt box not available' }
    } else {
      ok = false
      error = `unknown command ${JSON.stringify(cmd)}`
    }
  } catch (err) {
    ok = false
    error = String(err)
  }
  await call($, 'POST', '/v1/result', { sid, cmd, ok, error })
  await report($)
}

// The session's line to the bridge: long-polls for knob commands while the
// session lives; backs off while the bridge is away.
async function commandLoop($: EngineInterface): Promise<void> {
  let greeted = false
  while (alive) {
    try {
      if (!greeted) {
        greeted = (await call($, 'POST', '/v1/hello', {
          sid, title, cwd: await $.session.cwd(),
          entrypoint: (await $.env.get('CLAUDE_CODE_ENTRYPOINT')) ?? '',
          version: await $.session.version(),
        })) !== null
        if (greeted) await report($)
      }
      const asked = await $.clock.now()
      const r = greeted ? await call($, 'GET', `/v1/commands?sid=${encodeURIComponent(sid)}&wait=${POLL_WAIT_S}`) : null
      if (r === null) {
        greeted = false
        await $.clock.sleep(RETRY_MS)
        continue
      }
      for (const cmd of r.commands ?? []) await runCommand($, cmd)
      // A bridge answering an empty poll at once must not turn this into a busy loop.
      if (!(r.commands ?? []).length && (await $.clock.now()) - asked < 1_000) await $.clock.sleep(1_000)
    } catch {
      return   // the module unloaded or the session ended
    }
  }
}

async function ask($: EngineInterface, body: Record<string, unknown>): Promise<any> {
  return call($, 'POST', '/v1/ask', { sid, ...body })
}

function toolInput(e: Record<string, unknown>): Record<string, unknown> {
  const { tool: _tool, tool_use_id: _id, agentId: _agent, ...input } = e
  return input
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    sid = await $.session.id()
    sock = await socketPath($)
    alive = true
    void commandLoop($)
    $.clock.every(HEARTBEAT_MS, () => report($))
    return started
  })

  on('session.end', async ($, e, next) => {
    alive = false
    await call($, 'POST', '/v1/bye', { sid })
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    turnId = e.turnId
    setState($, THINKING)
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    turnId = undefined
    setState($, e.reason === 'answer' ? DONE : e.reason === 'aborted' ? IDLE : ERROR)
    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    setState($, COMPACTING)
    const done = await next(e)
    setState($, THINKING)
    return done
  })

  on('classic.UserPromptSubmit', async ($, e, next) => {
    mode = String(e.permission_mode ?? mode)
    effort = String((e as any).effort ?? effort)
    title = String((e as any).session_title ?? title)
    promptedAt = await $.clock.now()
    void report($, true)   // typed here: the knob follows this session
    return next(e)
  })

  // Questions: answered on the knob, else Claude Code's own dialog.
  on('classic.PreToolUse', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    setState($, ASKING)
    const input = toolInput(e as any)
    const r = await ask($, { what: 'question', input })
    setState($, THINKING)
    if (r?.answers) return { allow: true, updatedInput: { ...input, answers: r.answers } }
    return next(e)
  }).catch(($, e, next) => next(e))

  // Plans: accepted or rejected on the knob, else the normal dialog.
  on('classic.PreToolUse', { tool: 'ExitPlanMode' }, async ($, e, next) => {
    setState($, ASKING)
    const input = toolInput(e as any)
    const r = await ask($, { what: 'plan', input })
    setState($, THINKING)
    if (r?.verdict === true) return { allow: true, updatedInput: input }
    if (r?.verdict === false) return { deny: 'Plan am Knob abgelehnt.' }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('classic.PreToolUse', async ($, e, next) => {
    if (e.tool !== 'AskUserQuestion' && e.tool !== 'ExitPlanMode') setState($, TOOL)
    return next(e)
  })

  on('classic.PostToolUse', async ($, e, next) => {
    setState($, THINKING)
    return next(e)
  })

  // Permission requests: the knob's decision, else the normal dialog. A
  // failure always falls back to the dialog, never to an allow.
  on('classic.PermissionRequest', async ($, e, next) => {
    if (e.tool_name === 'AskUserQuestion' || e.tool_name === 'ExitPlanMode') return next(e)
    setState($, NEEDS_PERMISSION)
    const r = await ask($, {
      what: 'permission', tool: e.tool_name, input: e.tool_input ?? {},
      suggestions: e.permission_suggestions ?? [],
    })
    setState($, TOOL)
    if (r?.decision) return { decision: r.decision }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('classic.Notification', async ($, e, next) => {
    const kind = String((e as any).notification_type ?? '')
    if (kind === 'idle_prompt') setState($, DONE)
    if (kind === 'permission_prompt') setState($, NEEDS_PERMISSION)
    return next(e)
  })
}
