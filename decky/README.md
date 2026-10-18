# Memcard for Decky

Steam Deck Game Mode front end for [Memcard](../README.md). It's a thin wrapper: every action runs the bundled `memcard` CLI with `--json`, and all sync logic lives there.

- **Around games.** When you press Play on a game mapped to a save group, the plugin cancels the launch, syncs (asking about any conflict in a modal), then starts the game. After Steam reports the game exited, it pushes the new saves at idle priority. Your shortcuts' launch options are never touched.
- **Quick Access panel.** Shows status and has Sync now, Resolve conflicts, Find EmuDeck saves, cloud setup, Install rclone and the log.
- **Runs as the `deck` user** (no root flag). It uses the same config, state and trash as `memcard` in a Desktop Mode terminal.

Build: `scripts/build-decky.sh` (from the repo root) → `dist/memcard-decky.zip`. Install it via Decky → Settings → Developer → Install plugin from ZIP file.
