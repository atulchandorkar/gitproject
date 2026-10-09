import type { CommandRunInput, ConfigRow, On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { pick } from './register'

// A /skin run as typed in the prompt box.
const skin = (args: string): CommandRunInput => ({
  command: 'skin', args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 },
})

// Stands in for the engine's settings: one theme row the hooks list and set.
function fakeTheme(on: On, opts: { value?: string; isLocked?: boolean; options?: string[] } = {}) {
  const row: { value: string } = { value: opts.value ?? 'dark' }
  const options = opts.options ?? ['dark', 'light', 'dark-daltonized', 'plugin:skin:dracula']
  on('config.list', async () => {
    const theme: ConfigRow = {
      key: 'theme', label: 'Theme', kind: 'choice', value: row.value, options,
      provider: { plugin: 'core', tier: 'core' } as unknown as ConfigRow['provider'], isLocked: opts.isLocked ?? false,
    }
    return { value: [theme] }
  })
  on('config.set', async ($, e) => {
    row.value = String(e.value)
    return { value: e.value }
  })
  return row
}

// Stands in for the disk: `shipped` are theme files inside the mod (any path
// outside the home folder ending in themes/<slug>.json), `home` the user's files.
function fakeDisk(on: On, shipped: Record<string, string>, home: Record<string, string> = {}) {
  mock.env(on, { HOME: '/home/u' })
  const find = (path: string): string | undefined => {
    if (path in home) return home[path]
    const slug = /\/themes\/([a-z0-9-]+)\.json$/.exec(path)?.[1]
    return !path.startsWith('/home/u/') && slug ? shipped[slug] : undefined
  }
  on('fs.exists', async ($, e) => ({ value: find(e.path) !== undefined }))
  on('fs.read', async ($, e) => {
    const text = find(e.path)
    return text === undefined ? { deny: 'ENOENT' } : { value: text }
  })
  on('fs.write', async ($, e) => {
    home[e.path] = e.text
    return { value: undefined }
  })
  return home
}

describe('pick', () => {
  const options = ['dark', 'light', 'dark-daltonized', 'custom:dracula', 'custom:midnight']

  test('finds a namespaced theme by its short name', () => {
    expect(pick(options, 'dracula')).toBe('custom:dracula')
    expect(pick(options, 'Dracula')).toBe('custom:dracula')
  })

  test('prefers an exact match', () => {
    expect(pick(options, 'dark')).toBe('dark')
  })

  test('returns undefined when nothing matches', () => {
    expect(pick(options, 'solarized')).toBe(undefined)
  })
})

describe('/skin', () => {
  test('lists themes when run bare', async ($, on) => {
    fakeTheme(on)
    const out = await $.command.run(skin(''))
    expect(out.text).toContain('Current theme: dark')
    expect(out.text).toContain('plugin:skin:dracula')
  })

  test('switches to Dracula', async ($, on) => {
    const row = fakeTheme(on)
    const out = await $.command.run(skin('dracula'))
    expect(out.text).toBe('Theme set to plugin:skin:dracula.')
    expect(row.value).toBe('plugin:skin:dracula')
  })

  test('/skin default goes back to dark', async ($, on) => {
    const row = fakeTheme(on, { value: 'plugin:skin:dracula' })
    await $.command.run(skin('default'))
    expect(row.value).toBe('dark')
  })

  test('an unknown theme changes nothing', async ($, on) => {
    const row = fakeTheme(on)
    fakeDisk(on, {})
    const out = await $.command.run(skin('no-such-theme'))
    expect(out.text).toContain('No theme matches')
    expect(row.value).toBe('dark')
  })

  test('a locked theme is left alone', async ($, on) => {
    const row = fakeTheme(on, { isLocked: true })
    const out = await $.command.run(skin('dracula'))
    expect(out.text).toContain('organization manages the theme')
    expect(row.value).toBe('dark')
  })

  test('installs the shipped Dracula theme when the list lacks it', async ($, on) => {
    const row = fakeTheme(on, { options: ['auto', 'dark', 'light'] })
    const files = fakeDisk(on, { dracula: '{"name":"Dracula"}' })
    const out = await $.command.run(skin('dracula'))
    expect(files['/home/u/.claude/themes/dracula.json']).toBe('{"name":"Dracula"}')
    expect(row.value).toBe('custom:dracula')
    expect(out.text).toContain('Installed /home/u/.claude/themes/dracula.json')
  })

  test('reuses a Dracula theme already installed', async ($, on) => {
    const row = fakeTheme(on, { options: ['dark'] })
    const files = fakeDisk(on, { dracula: 'new' }, { '/home/u/.claude/themes/dracula.json': 'mine' })
    const out = await $.command.run(skin('dracula'))
    expect(out.text).toBe('Theme set to custom:dracula.')
    expect(row.value).toBe('custom:dracula')
    expect(files['/home/u/.claude/themes/dracula.json']).toBe('mine')
  })
})
