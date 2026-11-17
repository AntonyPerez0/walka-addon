# Walka companion

The Windows app that links your walking pad to the Walka addon: it reads the
addon's signal off the screen, drives the treadmill over Bluetooth, records
your walks and serves a local dashboard.

```
py -m pip install -e .
py -m walka            # run it; the dashboard opens at http://127.0.0.1:8787
py -m walka --demo     # no hardware: simulated treadmill and game character
py -m walka probe      # report of what your treadmill exposes (sends it no commands)
py -m walka probe --all    # the same, also listening to the vendor's own channels
py -m walka beacon     # check the addon's signal can be read from your screen
py -m walka scan       # list nearby Bluetooth treadmills
```

See the [project README](../README.md) for setup, safety and how it works.
