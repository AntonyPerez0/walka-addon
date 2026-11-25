# Troubleshooting

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Troubleshooting

| Symptom | Try |
|---|---|
| The belt does not start, stop or change speed from Walka | It is not meant to: Walka only listens. Use the treadmill's remote or buttons. See [Walka only listens](HOW-IT-WORKS.md#walka-only-listens). |
| Treadmill not found | Is it on? Close the treadmill's own phone app (force-quit it). Remove the treadmill from Windows *Bluetooth & devices* if you paired it there. A USB Bluetooth 5 adapter helps with weak built-in radios. |
| Treadmill not found, on a Mac | *System Settings → Privacy & Security → Bluetooth*: is **Walka** switched on? Then close Walka and open it again. |
| Treadmill not found, and it is not a UREVO or WELLFIT | Give it the whole scan (15 seconds), or press *Scan* on the dashboard and pick it. If it is not in the list either, it does not speak Bluetooth FTMS - see [which treadmills work](TREADMILLS.md#which-treadmills-work). |
| It connects, but the dashboard shows no speed while the belt moves | The treadmill may not send standard treadmill data. Send a `Walka.exe probe` report. |
| The treadmill's own phone app no longer connects | Most treadmills accept one connection at a time, and Walka has it. Press *Disconnect* on the dashboard (or close Walka) first. |
| My walk is not filed under my character (it says *No game session*) | Walka hears from the game only when the game saves its file: log out or type `/reload`, and give it a few seconds. Then run `Walka.exe game` (`Walka game` on a Mac): it says whether the game's file was found, when the game last wrote it, and the newest walking session in it. (Your walk is recorded all the same - it is only not filed under a character yet.) |
| `Walka.exe game` says World of Warcraft was not found | Walka finds the game where the addon is installed. Install the addon from the dashboard, or choose its folder under *Settings → Game addon*. |
| `Walka.exe game` says the game has not saved anything for Walka | Is the addon enabled (*AddOns* on the character screen)? Log in, then log out or type `/reload`: that is when the game writes the file. |
| The game crashed, and my walk is not filed under my character | The game saves the addon's file at a logout, a `/reload` or a disconnect - not when it crashes - so the session since the last save is lost. The walk itself is kept, under *No game session*. |
| The newest session is there, but the walk still is not filed under it | A walk is filed under a session when they overlap for at least half of the shorter of the two: a walk done mostly before the session began or after it was stopped is not. Was a session running (`/walka status`) while you walked? |
| Walka opens in my web browser, with a small box that says *Walka is running* | That Windows has no *Microsoft Edge WebView2*, which Walka's own window is drawn with. Walka works the same in the browser; the box is how you close it. To get the window: install the *WebView2 Runtime* from Microsoft. See [Walka's window](INSTALL.md#walkas-window). |
| I open Walka and nothing happens | Is it open already? Look on the taskbar (or in the Dock). Otherwise `walka.log`, in the folder with [your settings and walks](INSTALL.md#walkas-window), says how far it got: please [open an issue](https://github.com/AntonyPerez0/walka-addon/issues) with it. |
| The floating window is not on top of the game | Run the game in *Windowed* or *Windowed (Fullscreen)* mode: nothing can be drawn over a game in exclusive fullscreen. |
| No floating window in Settings | It needs Walka's own window: not with `--browser`, `--no-window` or the dashboard in the web browser. |
| Addon marked out of date after a game patch | Tick *Load out of date AddOns*, and update the `## Interface:` line in `Walka/Walka.toc`. |
| Walks are no longer recorded after the pad was switched off and on | The companion connects again by itself when it hears the pad come back on after at least 20 seconds of silence, or when you press *Connect* on the dashboard. (A session starting in the game no longer makes it connect: the companion does not see the game while it runs.) If none of that happens, run *Check my treadmill* and post the report. |
| The pad keeps waking up | While the pad is off or asleep, the companion only listens for it - it does not try to connect, which is what can wake a pad. It connects when it starts, when you press Connect, or when the pad has been silent for 20 seconds and is heard again (switched off and on). Closing the companion releases the pad entirely. |
