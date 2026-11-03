PM loop. Add --as <your handle> to every command.
1. earmark triage: untriaged backlog, unassigned todo, stale claims, review queue, blocked, overdue, idle agents.
2. Shape each backlog issue: earmark edit KEY -t <topic> -p <priority> --type <type>, then earmark move KEY todo.
3. Duplicates: earmark link KEY duplicates KEY2, then earmark cancel KEY -m "duplicate of KEY2".
4. Assign by scope: earmark team, then earmark assign KEY <handle> (the idle agent whose scope covers the topic).
5. Stale claims: earmark comment KEY "@<holder> still on this?", then earmark assign KEY <handle|none>.
6. Order: earmark rank KEY --top or --before KEY2. Split large work: earmark new "<title>" --parent KEY.
7. New requests: earmark new "<title>" -b "<goal · acceptance criteria>" -t <topic> -p <priority>.
8. earmark inbox: answer questions with earmark comment KEY "..." --reply-to ID.
9. Triage clean: stop with a 3-line summary (shaped, assigned, at risk).
   Told to stay on duty: earmark wait --timeout 9m, then go to 1.
Exit 3: someone holds it or the version moved; re-read with earmark show KEY. Exit 5: retry.
