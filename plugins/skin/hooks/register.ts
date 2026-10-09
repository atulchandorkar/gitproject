import type { ConfigRow, EngineInterface, Register } from 'claude-code'

// Words that put the theme back to the built-in dark preset.
const RESET = ['default', 'reset', 'off']

// Picks the theme option the person meant: an exact value first, then one
// whose value ends with `:<word>` (`custom:dracula`, a plugin-namespaced id),
// then any that contains the word, ignoring case.
export function pick(options: readonly string[], word: string): string | undefined {
  const w = word.toLowerCase()
  return (
    options.find(o => o.toLowerCase() === w) ??
    options.find(o => o.toLowerCase().endsWith(`:${w}`)) ??
    options.find(o => o.toLowerCase().includes(w))
  )
}

// Copies themes/<slug>.json from this mod into the user's themes folder, unless
// a file of that name is already there. Undefined when the mod ships no such theme.
async function installShipped($: EngineInterface, slug: string): Promise<{ path: string; isNew: boolean } | undefined> {
  if (!/^[a-z0-9-]+$/.test(slug)) return undefined
  const source = `${$.plugin.root}/themes/${slug}.json`
  if (!(await $.fs.exists(source))) return undefined

  const configDir = (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${await $.env.get('HOME')}/.claude`
  const path = `${configDir}/themes/${slug}.json`
  if (await $.fs.exists(path)) return { path, isNew: false }

  await $.fs.write(path, String(await $.fs.read(source)))
  return { path, isNew: true }
}

function listing(row: ConfigRow): string {
  const options = row.options ?? []
  return `Current theme: ${String(row.value)}\nAvailable: ${options.join(', ')}\nUse /skin <name>, or /skin default.`
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'skin',
      description: 'Switch the colour theme: /skin dracula, /skin default, or /skin to list them',
      argumentHint: '[theme]',
    })

    return next(e)
  })

  on('command.run', { command: 'skin' }, async ($, e) => {
    const row = (await $.config.list()).find(r => r.key === 'theme')
    if (!row) return { text: 'This client has no theme setting for /skin to change.' }

    const word = e.args.trim()
    if (!word) return { text: listing(row) }
    if (row.isLocked) return { text: 'Your organization manages the theme, so /skin cannot change it.' }

    const options = row.options ?? []
    let target = RESET.includes(word.toLowerCase()) ? (pick(options, 'dark') ?? 'dark') : pick(options, word)
    let note = ''
    if (!target) {
      // Not listed yet: if this mod ships the theme, install it where Claude Code
      // reads custom themes (~/.claude/themes/<slug>.json, chosen as custom:<slug>).
      const installed = await installShipped($, word.toLowerCase())
      if (!installed) return { text: `No theme matches "${word}".\n${listing(row)}` }
      target = `custom:${word.toLowerCase()}`
      note = installed.isNew
        ? `\nInstalled ${installed.path}. If the theme doesn't show, restart Claude Code once (it picks up a new themes folder at start).`
        : ''
    }

    const result = await $.config.set({ key: 'theme', value: target })
    if ('deny' in result && result.deny) return { text: `Theme not changed: ${result.deny}${note}` }

    return { text: `Theme set to ${target}.${note}` }
  })
}
