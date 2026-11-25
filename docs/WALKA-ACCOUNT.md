# Your Walka account

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Your Walka account (on walka.dev)

Optional. Walka works in full without it, and if you never sign in it
sends nothing and asks nobody.

On Walka's website, <https://walka.dev/>, you can have a Walka account. It
holds your walks - and, if you put them there, body numbers and a food
diary - and its page is private until you make it public. Walka can send
your walks to it. They are then kept in the database the website's owner
runs; the website's [privacy page](https://walka.dev/privacy/) says who
else has a hand in that.

* **Signing in.** Dashboard → *Settings → Walka account* → *Sign in with
  Discord*, *GitHub* or *Google*. Your web browser opens: sign in there,
  then come back. Signing in makes a Walka account if you have none
  ([terms](https://walka.dev/terms/),
  [privacy](https://walka.dev/privacy/)). A Walka account's first step -
  *I am 18 or older, and I have read the terms and the privacy page* - is
  taken on [walka.dev/account/](https://walka.dev/account/): Walka does
  not tick it for you, and until it is taken sending cannot be switched
  on.
* **Nothing is sent until you switch that on.** Signing in does not do
  it. *Send my walks to my Walka account...* asks first: it names the
  Walka account, says what goes and how many walks, and lets you choose -
  all of them, or only walks from now on. From then on every walk
  recorded on this computer goes to that Walka account, whoever walked.
* **What goes,** of each walk: when it started and ended, the day, and
  how far your computer's clock was from world time; how long the belt
  moved; distance, steps and calories (estimates), and how many of the
  calories were on top of sitting; how each number was come by (for the
  calories: from speed or from heart rate - the word, not the heart
  rate); whether the walk was filed under a session in the game when it
  went (the game says so only when it saves, at a logout or /reload); and
  the walk's name on your computer, which is its start time. Each walk goes
  under a random number made for it, and says that it came from Walka.
  Every request names Walka and its version and says nothing else about
  your computer - but, like anything asked over the internet, it arrives
  with your IP address.
* **What stays on your computer:** the samples (speed and heart rate
  every five seconds), the heart rate, the top speed, the treadmill's
  name, your character, its name, its realm and its road, and your
  weight, height, age and sex. A walk made with the simulated treadmill
  (as with `--demo`) is never sent, and while Walka runs with that treadmill
  *Walka account* is not in Settings at all. A walk your Walka account
  does not take - one that lasts longer than two days, say - is not sent
  either: the dashboard says which.
* **When.** When Walka is started, and after each walk is saved, without
  asking again. (A walk that is saved as Walka closes goes at the next
  start. When there is no answer, Walka tries again later.) If your page
  on walka.dev is public, a walk counts on it as soon as it is sent.
* **To stop:** *Stop sending* in the same place, or *Sign out*. Either
  takes effect at once, and neither deletes anything in your Walka
  account.
* **Deleting on one side deletes nothing on the other.** A walk you
  delete on the dashboard stays in your Walka account until you delete it
  there ([walka.dev/account/walks/](https://walka.dev/account/walks/)). A
  walk you delete there stays on your computer and is not sent again.
  Nothing your Walka account does changes or deletes a walk on your
  computer.

**The sign-in Walka keeps is not a key for walks. It is the whole Walka
account:** whoever has it can read the body numbers and the food diary
there, make your page public and delete the Walka account.

* **Where it is kept:** in Windows' Credential Manager, or in your Mac's
  keychain - never in a file of Walka's. On a system that has neither
  (Linux, from source) it is kept in memory, and you are signed in until
  Walka is closed. While sending is switched on, Walka keeps a mark
  beside it, as a second entry: a random text that is a key to nothing.
  The same mark is in `walka-account.json`, and a walk goes only while
  the two are the same - so an older copy of that file, put back from a
  backup, does not by itself switch sending on again. *Stop sending* and
  signing out take the mark out.
* **Signing out in Walka removes it.** If Windows or your Mac does not
  let Walka take it out, Walka says so under *Walka account*, with a
  button that tries again: until that works, the sign-in may still be
  there. Deleting `Walka.exe` (or `Walka` on a Mac) does not remove it:
  sign out first. (If Walka is gone already, or cannot take it out, the
  entry can be deleted by hand, in Windows' Credential Manager or in
  Keychain Access on a Mac: its name begins `Walka account`. There may be
  two such entries, the sign-in and the mark: delete both.)
* **"Sign out" on walka.dev does not sign this computer out.** A computer
  stays signed in until somebody signs out on it, in Walka, or the Walka
  account is deleted.
* **A proxy set for your system is not used.** Walka makes its requests
  directly, and asks nobody whose certificate it cannot check. Where the
  internet is reached only through a proxy, Walka cannot sign in or send.
