# skin

A Claude Code mod that adds `/skin` and ships a **Dracula** colour theme.

| Command | Does |
|---|---|
| `/skin` | Shows the current theme and the ones available |
| `/skin dracula` | Switches to Dracula (or any installed theme, by name) |
| `/skin default` | Back to the built-in dark theme |

It changes the same `theme` setting as `/theme` and `/config`, and leaves a theme your
organization manages alone. The first `/skin dracula` copies the bundled theme to
`~/.claude/themes/dracula.json` (never overwriting one you already have) and selects
`custom:dracula`; if that themes folder is new, restart Claude Code once so it picks it up.

## Install

In a terminal running Claude Code:

```
/plugin install skin --marketplace atulchandorkar/gitproject
```

Answer `y` to add the marketplace, then pick the user scope. The theme applies to Claude Code's
interface; the `/plugin` command is a terminal one, but a mod installed at the user scope also
loads in the desktop app's local Code sessions.

## Develop

```
claude plugin validate plugins/skin
claude plugin test plugins/skin
```
