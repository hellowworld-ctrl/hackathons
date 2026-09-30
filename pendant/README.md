# Pendant

a band of ai agents writes a song together and hands you a midi file.
composer lays out the blueprint (key, tempo, sections, one chord per bar).
drums → bass → chords → melody each write their part, hearing what came before.
a plain-code validator checks every bar. a critic loop sends specific bars back for fixes.

[repo](https://github.com/Pranjal101Shrivastava/pendant_music/tree/codex/complete-pendant) · [outputs](https://github.com/Pranjal101Shrivastava/pendant_music/tree/codex/complete-pendant/outputs)

## outputs

five prompts, 16 bars each, generated with the openai provider.

| prompt | key | bpm | sections | model calls | midi |
|---|---|---|---|---|---|
| sad lo-fi | A minor | 80 | intro · verse · outro | 9 | [song.mid](https://github.com/Pranjal101Shrivastava/pendant_music/blob/codex/complete-pendant/outputs/sad-lo-fi-80-bpm/song-sad-lo-fi-80-bpm.mid) |
| dreamy ambient | C major | 70 | intro · main · outro | 7 | [song.mid](https://github.com/Pranjal101Shrivastava/pendant_music/blob/codex/complete-pendant/outputs/dreamy-ambient-70-bpm/song-dreamy_ambient-70-bpm.mid) |
| bright pop | C major | 100 | intro · verse · chorus · outro | 5 | [song.mid](https://github.com/Pranjal101Shrivastava/pendant_music/blob/codex/complete-pendant/outputs/bright-pop-100-bpm/song.mid) |
| driving rock | E minor | 120 | intro · verse · chorus · outro | 5 | [song.mid](https://github.com/Pranjal101Shrivastava/pendant_music/blob/codex/complete-pendant/outputs/driving-rock-120-bpm/song.mid) |
| energetic jazz | Bb major | 130 | intro · head a · bridge · outro | 5 | [song.mid](https://github.com/Pranjal101Shrivastava/pendant_music/blob/codex/complete-pendant/outputs/energetic-jazz-130-bpm/song-energetic-jazz-130-bpm.mid) |

each folder also has `song.json` (blueprint + notes) and `report.json` (call counts, retries, repairs, critic rounds).
