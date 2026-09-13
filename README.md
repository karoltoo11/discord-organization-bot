# Discord Organization Bot

This is my Discord organization bot. I built it to handle server organization, matchmaking (LFG), personal reminders, to-do task boards, contests, events, and moderation traps. It was a really cool experience for me; I had to figure out a lot of tricky things, especially persistent buttons, background schedulers, and SQLite database handling.

<h2>Discord server with bot:</h2>

- https://discord.gg/YZ4ec83nKM

## How to Use:

1. Invite the bot or join the Discord server.
2. All slash commands are listed below or can be checked directly by typing `/help`.
3. Go to your bot-commands channel and type your first command!

## All commands

### Looking For Group (LFG)
- `/lfg <game> <date> [slots]` - Creates a matchmaking post with Join/Leave buttons.
- `/end_lfg <message_id>` - Closes the LFG post early (host or admin).
- `/lfg_list <message_id>` - Shows registered players for the match.

### To-Do Task Board
- `/todo add <content> [priority]` - Adds a task to the board (High, Medium, Low).
- `/todo list [filter]` - Shows server tasks (To Do, Done, All).
- `/todo done <task_id>` - Toggles task status between Done and To Do.
- `/todo assign <task_id> [user]` - Assigns a task to yourself or someone else.
- `/todo delete <task_id>` - Deletes a task from the board.

### Personal Reminders
- `/remind <when> <content> [delivery]` - Sets a personal reminder (e.g. 15m, 2h, 20:30, DD.MM.YYYY HH:MM).
- `/reminders` - Shows your active reminders.
- `/cancel_reminder <reminder_id>` - Cancels a scheduled reminder.

### Contests and Giveaways
- `/contest <name> <end_date> <prize> [limit] [winner_count]` - Creates a contest with Join/Leave buttons.
- `/end_contest <message_id>` - Ends a contest early and rolls winners.
- `/reroll <message_id> [winner_count]` - Rerolls winners from the participants.
- `/participants <message_id>` - Shows who joined the contest.

### Server Events
- `/event <name> <date> <description> [limit] [create_thread]` - Creates an event with signups.
- `/start_event <message_id>` - Starts the event early and DMs registered members.
- `/list <message_id>` - Shows registered members for the event.
- `/edit_event <message_id> [name] [date] [description] [limit]` - Edits event details.

### Trap Channels (Admin only)
- `/trap set [channel]` - Turns a channel into a trap (sending any message = instant kick).
- `/trap remove [channel]` - Removes the trap from a channel.
- `/trap list` - Shows all active trap channels.

### Info
- `/help` - Shows the interactive help menu.

## Translation

Unfortunately, English is not my native language, so I used Google Translate for all translations without altering them in any way. I did this to avoid unnecessary typos and the like.
