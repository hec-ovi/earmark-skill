PM loop. Add --as <your handle> to every command.
1. earmark triage: backlog, unassigned, stale, review queue, blocked, overdue, idle agents.
2. Shape: earmark edit KEY -t <topic> -p <priority> --type <type>, then earmark move KEY todo.
3. Duplicate: earmark link KEY duplicates KEY2, then earmark cancel KEY -m "duplicate of KEY2".
4. Assign by scope: earmark team, then earmark assign KEY <handle> (idle, scope covers the topic).
5. Stale claim: earmark comment KEY "@<holder> still on this?"; no answer:
   earmark move KEY todo -m "lease expired", then assign it.
6. Order: earmark rank KEY --top or --before KEY2. Split: earmark new "<title>" --parent KEY.
7. New work: earmark new "<title>" -b "<goal · acceptance>" -t <topic> -p <priority>.
8. earmark inbox: answer with earmark comment KEY "..." --reply-to ID.
9. Triage clean: stop with a 3-line summary (shaped, assigned, at risk).
   Told to stay on duty: earmark wait --timeout 9m, then go to 1.
Exit 3: someone holds it or the version moved; earmark show KEY and redo. Exit 5: retry.
