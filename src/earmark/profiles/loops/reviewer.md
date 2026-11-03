Reviewer loop. Add --as <your handle> to every command.
1. earmark next --review: claim the next handoff (never your own). Exit 4: go to 5.
2. earmark comments KEY --new: read the handoff note, then verify exactly what it claims.
3. Pass: earmark approve KEY -m "<what you checked>". Then go to 1.
4. Fail: earmark reject KEY -m "<each defect: file:line, expected, actual>". Then go to 1.
5. earmark inbox: answer replies with earmark comment KEY "..." --reply-to ID.
6. Nothing left: stop with a 3-line summary (approved, rejected, waiting).
   Told to stay on duty: earmark wait --for review --timeout 9m, then go to 1.
Out-of-scope problems: earmark new "<title>" -b "<evidence>" -t <topic>. Never fix the code yourself.
Exit 3: another reviewer holds it, take another. Exit 5: retry. Ending the session: earmark leave
