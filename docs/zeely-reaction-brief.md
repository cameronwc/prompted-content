# Zeely reaction clips: brief and prompts

These clips are the top half of the short-form "UGC" videos. The app action
plays underneath, the hook text sits over the reaction for the first second
and a half. The person never speaks a product claim; the hook carries the
words. Every post is labelled AI-generated on TikTok and Instagram.

## Settings, every clip

- Format: vertical 9:16, 1080x1920 (or the largest Zeely allows).
- Length: 6 seconds. The composer holds the last frame if the app clip runs longer.
- No on-screen text, no captions, no logo, no music.
- No dialogue. Mouth movement is fine (a "huh", a laugh) but no words.
- Framing: chest up, eyes slightly down and to one side as if looking at a
  phone held out of frame, then up to camera for the reaction.
- Lighting: mixed. Half daylight (window, porch, outdoors), half indoor.
- Backgrounds: plain and real. A living room, a studio wall, a car, a park.
- People: working portrait photographers, 25 to 45, mixed gender and
  ethnicity, no two clips the same person. Neutral clothes. A camera strap
  over the shoulder or a camera on the table sells it without a word.
- Export: highest quality MP4, H.264.
- File names (matters for the composer, the emotion is the last word):
  `reaction-01-skeptical.mp4`, `reaction-02-confused.mp4`, and so on.

## The fifteen clips

Three per emotion. Copy the prompt, change the person and setting each time.

### Skeptical (files end in `-skeptical`)

1. A woman in her thirties, camera strap over one shoulder, sitting on a
   porch step in daylight. She looks down at a phone out of frame with her
   arms loosely crossed, unconvinced. After two seconds her eyebrows lift a
   little and she tilts her head: a reluctant "huh." Holds on camera. No words.
2. A man in his forties in a small home studio, softbox behind him. Looking
   at the phone with a flat expression, one eyebrow raised. Slowly he nods
   once, still not smiling, like he's been proven wrong and doesn't love it.
3. A woman in her twenties in the driver's seat of a parked car, daylight
   through the window. Skeptical squint at the phone, then a small shrug and
   a "fine, okay" mouth shape. No words.

### Confused (files end in `-confused`)

4. A man in his thirties at a kitchen table, camera body on the table.
   Reading the phone with a frown, lips slightly parted, genuinely lost.
   Then it lands: eyes widen a touch, the frown clears. No words.
5. A woman in her forties standing in a park, overcast. Squinting at the
   phone, head pulled back an inch, "wait, what?" Then a slow understanding
   nod.
6. A man in his twenties on a couch, lamp light. Confused blink, looks up
   at the camera, looks back down, reads it again. Ends on a half smile.

### Impressed (files end in `-impressed`)

7. A woman in her thirties leaning on a studio wall, camera hanging at her
   hip. Reads the phone, eyebrows go up, she presses her lips together and
   nods, impressed despite herself. Looks up to camera.
8. A man in his forties on a porch at golden hour. A quiet "oh" face, then
   a real smile and a small point at the phone with his free hand.
9. A woman in her twenties at a cafe table by a window. Reads, does a slow
   "not bad" nod with a downturned appreciative mouth, then looks at the
   camera like "you should see this."

### Laughing (files end in `-laughing`)

10. A man in his thirties in a car, daylight. Reads the phone and laughs,
    caught off guard, head back briefly, then wipes an eye. No words.
11. A woman in her forties in a home studio. A snort laugh she tries to
    hold in, hand to her mouth, shoulders shaking, then a grin at camera.
12. A man in his twenties outdoors, leaning on a fence. Reads, exhales a
    laugh through his nose, shakes his head smiling, looks up.

### Relieved (files end in `-relieved`)

13. A woman in her thirties sitting on the floor against a couch, camera
    beside her, tired. Reads the phone, closes her eyes for a beat, and lets
    out a long breath, shoulders dropping. A small grateful smile.
14. A man in his forties in a studio at the end of a day, lights off behind
    him. Reads, rubs his forehead once, then nods slowly, "finally." No words.
15. A woman in her twenties outdoors in a field, wind in her hair. Reads,
    a relieved laugh, hand over her heart, then a thumbs up to camera.

## Where they go

Drop the files in `dist/reactions/` in this repo. The composer picks a
reaction whose emotion matches the hook, so keep the file names honest.
Then `make ugc-dry-run` to preview three, `make ugc-generate` for a batch.
