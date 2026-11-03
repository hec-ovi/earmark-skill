Reviewer loop. Add --as <your handle> to every command.
1. earmark next --review: claim the next handoff (never your own); it prints the issue and the handoff note. Exit 4: go to 4.
2. Verify exactly what the note claims: run its tests or steps, read every changed file.
3. Pass: earmark approve KEY -m "<what you checked>" --next. Fail: earmark reject KEY -m "<each defect: file:line, expected, actual>" --next.
   Never fix the code yourself. The third reject parks the issue for the pm or owner.
4. Nothing to review: earmark check, handle what needs you.
5. Nothing left: earmark leave, then a 3-line summary. Told to stay on duty: earmark wait --timeout 2m, then go to 1 (`wait: board` means nothing is open: stop).
Out-of-scope problems: earmark new "<title>" -b "<evidence>" -t <topic>. Exit 3: another reviewer holds it, take another. Exit 5: retry.
