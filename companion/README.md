# Walka companion

The app - for Windows and macOS - that goes with the Walka addon: it reads
the addon's signal off the screen, listens to your treadmill over Bluetooth,
records your walks and serves a local dashboard.

It only listens to the treadmill. It cannot start or stop the belt or
change its speed: you do that at the treadmill itself.

```
py -m pip install -e .
py -m walka            # run it; the dashboard opens at http://127.0.0.1:8787
py -m walka --demo     # no hardware: simulated treadmill, walker and game character
py -m walka probe      # report of what your treadmill exposes (sends it no commands)
py -m walka probe --all    # the same, also listening to the vendor's own channels
py -m walka beacon     # check the addon's signal can be read from your screen
py -m walka scan       # list nearby Bluetooth treadmills
```

On a Mac it is `python3` in place of `py`, and macOS will ask whether
Terminal may use Bluetooth and record the screen: see
[On a Mac](../README.md#on-a-mac).

See the [project README](../README.md) for setup and how it works, and
[docs/SAFETY.md](../docs/SAFETY.md) for what Walka does to a treadmill (it
listens) and what it does not.

Walking while you play is at your own risk, and Walka comes with no
warranty: see the [disclaimer](../DISCLAIMER.md).
