"""Sending walks to a Walka account (on walka.dev): signing in, and the sender.

Walka works in full without this, and by default none of it is there:
the part is handed to the ``App`` only when Walka runs from its source
code with the accounts' address and key set on that computer
(``cli._build_walka_account``).

When it may open a connection to another machine: only when the person
has signed in, sending is switched on for that account, and there is a
walk to send - or when the person has just pressed a button of the
Walka-account card that says it asks the account. Never signed in means
no request of any kind and no look into the system's store for secrets.
There is no timer.

The keys of a sign-in live in memory, and the refresh token alone in the
system's store for secrets (``keystore``). They are in no file and in no
line of the log. Beside the refresh token the store holds one more thing
while sending is on: a mark, a random text that is a key to nothing and
also stands in ``walka-account.json``, so that the file alone cannot say
"sending is on". Nothing here deletes or changes a walk on the computer.
"""
