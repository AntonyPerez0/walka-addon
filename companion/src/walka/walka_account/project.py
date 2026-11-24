"""Where the Walka accounts are: the one place in Walka that says so.

The address of the project that keeps the Walka accounts (on walka.dev),
and that project's PUBLISHABLE key. Both are public by design: the
website's own pages carry the same two. What guards a Walka account is
the person's own sign-in and the rules in the database, not this key,
which only says which project is asked. It is never a secret key: such a
key passes every rule of the database and has no business on anybody's
computer (``https.key_is_usable`` turns one down).

The two stand here and nowhere else - not in a test, a document or the
build. A test reads this file to see that both are of the right kind,
and looks through every other file for them.

That Walka knows the address does not mean that it asks there. It does
so only when somebody has signed in, sending is switched on for that
Walka account and there is a walk to send - or when the person has just
pressed a button that says it asks (see the package's own words).
"""

from __future__ import annotations

ADDRESS = "https://avejvgygkujbzjwqbdug.supabase.co"
PUBLISHABLE_KEY = "sb_publishable_ZBWVidwBsg0j4OFWbJO0EA_uuhv1JX_"
