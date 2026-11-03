Worker loop. Add --as <your handle> to every command.
1. earmark next: claim your best ready issue. Exit 4: go to 6.
2. earmark comments KEY --new: catch up, then read only the code the issue needs.
3. earmark comment KEY "<done, next>" --kind progress: at each milestone; renews your lease.
4. earmark move KEY blocked -m "<blocked by what>": only when you cannot go on; then go to 1.
5. earmark review KEY -m "<what changed> · <files> · <how to verify>": then go to 1.
6. earmark next --review: review someone else's handoff (approve or reject); then go to 1.
7. earmark inbox: answer what is addressed to you with earmark comment KEY "..." --reply-to ID.
8. Nothing left: stop with a 3-line summary (done, in review, open or blocked).
   Told to stay on duty: earmark wait --timeout 9m, then go to 1.
Exit 3: someone else holds it, take another. Exit 5: retry. Ending the session: earmark leave.
