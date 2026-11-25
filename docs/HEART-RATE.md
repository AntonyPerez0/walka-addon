# Heart rate

[← Back to the README](../README.md)

Optional. Walka reads heart rate from any device that sends it as a
standard Bluetooth heart-rate sensor (the Heart Rate service, `0x180D`): a
chest strap, an arm band, or a watch with broadcasting switched on. Pick it
under *Settings → Heart-rate sensor → Scan for sensors*: each sensor found
shows its heart rate right now, so you can tell which is yours. Walka
remembers it, also when you turn it off.

* Readings the sensor marks as taken without skin contact are not used.
* The sensor's battery level is shown when it reports one.
* With age and sex in the body profile, calories are worked out from heart
  rate.

## Switching broadcasting on, maker by maker

From each maker's own help pages, as they read in November 2026. The same
steps are in the dashboard, under *How to switch on heart-rate
broadcasting*.

| Maker | How | Good to know | Source |
|---|---|---|---|
| **Garmin** | *Settings → Health & Wellness* (some models: *Watch Sensors*) *→ Wrist Heart Rate → Broadcast Heart Rate*, or the broadcast control in the controls menu; then Start. Many models can also broadcast during an activity (an activity setting). | Only models on Garmin's Bluetooth list broadcast over Bluetooth (others ANT+ only, which Walka cannot read). Uses more battery. | [Devices that broadcast heart rate via Bluetooth](https://support.garmin.com/en-US/?faq=Zj1947s6pqAHzBCAhLhrC9) |
| **Polar watches** | Pre-training mode → *Settings → Sport settings → Heart rate → Share heart rate* (Vantage V3, Grit X2 Pro, Ignite 3), or quick menu → *Share HR with other devices* (Pacer, Ignite 2, Unite). | Shares only in pre-training or training mode. Stop sharing with the phone first. | [HR sensor mode](https://support.polar.com/en/hr-sensor-mode) |
| **Polar H10, Verity Sense** | Wear it. | One other app at a time (two with the dual-Bluetooth setting). | [Polar](https://support.polar.com/en/support/can_i_pair_h10_with_several_devices) |
| **Google Pixel Watch 2 and newer** | Swipe down → *Connected Fitness → Connect* (or *Settings → Connectivity → Share Heart Rate*); accept on the watch. | Disconnect from other equipment first. | [Google](https://support.google.com/googlehealth/answer/14236705) |
| **Fitbit Charge 6** (and the models Google lists) | Swipe down → *Broadcast HR*, then *Share → Start* on the tracker. | One device at a time. | [Google](https://support.google.com/googlehealth/answer/14236705) |
| **COROS** | Hold BACK → *Toolbox → Broadcast HR*. | One Bluetooth connection while broadcasting: switch off the phone's Bluetooth first. | [COROS](https://support.coros.com/hc/en-us/articles/360040256991-Broadcasting-Heart-Rate) |
| **Suunto** (Vertical 2, Race 2, Race, Race S) | *Broadcast* on in a sport mode's settings; start that sport mode. Shows up as "Suunto watch HR". | Only while that sport mode is in use. | [Suunto feature feed](https://us.suunto.com/pages/feature-feed) |
| **Amazfit Helio Strap** | Zepp app → *Device → Amazfit Helio Strap → Health Monitoring → Heart Rate Push*. | Amazfit's pages describe no such setting for its watches. | [Amazfit](https://support.amazfit.com/us/amazfit_helio_strap/docs/GyUIdtHLvoMqNUxOkuNcQB4hn4d) |
| **Wahoo TICKR** | Wear it. | Older TICKRs: one Bluetooth connection at a time. Wahoo recommends ANT+ on Windows; Walka reads Bluetooth. | [Wahoo](https://support.wahoofitness.com/hc/en-us/articles/115000320290-Wahoo-TICKR-Product-Instructions) |

A Walka user has tried a Garmin watch with Walka; how that went is not
written down yet. None of the others has been tried with Walka. If one
works - or does not - please say so in a ["My heart-rate sensor"
issue](https://github.com/AntonyPerez0/walka-addon/issues/new?template=my-heart-rate-sensor.yml).
