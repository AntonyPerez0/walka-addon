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

## Heart rate from an Apple Watch or a Galaxy Watch

Neither has a built-in standard Bluetooth heart-rate broadcast: Apple
connects its watch only to "GymKit" equipment
([Apple](https://support.apple.com/guide/watch/use-gym-equipment-apd15b0268fd/watchos)),
and watchOS apps cannot advertise Bluetooth services themselves
([Apple's developer documentation](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager));
Samsung's pages describe no such feature. Watch apps from other makers can
add one. What their store pages said in November 2026 - **none has been
tried with Walka**, and Walka's author has no tie to any of them:

**Apple Watch** (these send the heart rate on through the iPhone, which
then acts as the Bluetooth sensor; keep the app open on both):

| App | What its listing says | Price (listing) |
|---|---|---|
| [ECHO - heart rate from the watch](https://apps.apple.com/us/app/echo-heart-rate-from-the-watch/id1494679144) | "broadcasts through your iPhone to equipment and apps that support standard Bluetooth heart rate monitors" | free for 20-minute workouts; Pro $9.99 or $19.99 |
| [HeartCast](https://apps.apple.com/us/app/heartcast-heart-rate-monitor/id1499771124) | broadcasts to "Bluetooth-enabled fitness equipment"; keep HeartCast visible on the iPhone when connecting | free; Pro $4.99 |
| [BlueHeart](https://apps.apple.com/us/app/blueheart-bluetooth-heart-rate/id1485503543) | "follow[s] the Bluetooth SIG spec for a heart rate monitor ... shows up exactly as a chest strap would" (last updated 2021) | free; Pro $9.99 |

Two more listings ([HeartBLE](https://apps.apple.com/us/app/heartble-heart-rate-bluetooth/id6758879454),
[Heart Rate Broadcast](https://apps.apple.com/us/app/id6473728264)) say the
watch connects directly; Apple's documentation says a watch app cannot
advertise, so expect the iPhone to be needed.

**Wear OS** (Galaxy Watch4 and newer, Pixel Watch, others - the watch
itself is the sensor; keep the app in front):

| App | What its listing says | Price (listing) |
|---|---|---|
| [Heart for Bluetooth](https://play.google.com/store/apps/details?id=lukas.the.coder.heartforbluetooth) | "standardized Bluetooth Low Energy protocol in the same way as any other chest strap"; can connect "to your PC". Wear OS 3.5+ (not Tizen); its [troubleshooting page](https://sites.google.com/view/heartforbluetooth/troubleshooting) says Galaxy watches change their Bluetooth address at every start, so pick it again in Walka's scan | free |
| [BLE Heartrate Transmitter](https://play.google.com/store/apps/details?id=de.hendrikgroove.bleheartratetransmitter) | "uses standard Bluetooth Heart Rate Profile (HRP)"; open source | $0.99 |
| [HR2VP Watch Bike Computer](https://play.google.com/store/apps/details?id=com.bipr.bike.hr2vpwatch) | "broadcast HR over Bluetooth Low Energy as a heart rate monitor during your workouts" | $11.99 |

Older Samsung watches (Tizen: Galaxy Watch, Watch3, Active) are not covered
by these.

## Heart rate over the local network

For a watch app that can send the heart rate to a web address instead.
*Settings → Heart-rate sensor → Heart rate over the local network*:

* **Off by default.** Nothing listens until you switch it on.
* **Its own port** (8788; changeable under *Advanced*), not the dashboard's,
  which stays reachable from this computer only. This port is reachable
  from your home network - that is its point - and does one thing.
* **A pairing code.** The dashboard shows the address to give the watch
  app, `http://<this computer>:8788/<code>/hr`; anything else is "not
  found", and after 10 wrong codes an address is locked out for ten
  minutes. *Make a new code* replaces it.
* It takes a heart rate and nothing else: a number (`72`), JSON such as
  `{"bpm": 72}` (also `heartRate`, `heart_rate`, `hr`, `value`), or
  `?bpm=72` on a GET.
* Your computer's firewall may ask whether Walka may be reached on the
  network: allow it on private (home) networks only.

A Bluetooth sensor that is connected and reading comes first. No watch app
has been tried with this yet; if yours can send to a web address, please
say how it went in a ["My heart-rate sensor"
issue](https://github.com/AntonyPerez0/walka-addon/issues/new?template=my-heart-rate-sensor.yml).
