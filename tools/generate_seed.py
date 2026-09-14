#!/usr/bin/env python3
"""Generate the 240-pose development catalog.

60 poses each in couples, senior, family, maternity; roughly a third of the
couples poses also carry `engagement`. Metadata is drawn from weighted,
category-appropriate distributions, and every pose gets three prompts in
distinct tones (always including `nervous_client`) drawn from hand-written
prompt banks.

The generator is deterministic (fixed RNG seed, fixed ULID timestamp) so
re-running it reproduces the same catalog. It refuses to touch any pose
directory whose record has `placeholder: false` — those are real poses.

Images are produced separately by tools/make_placeholders.py, which also
back-fills the real blurhash into each pose.yaml.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from random import Random

import yaml
from ulid import ULID

from common import POSES_DIR, load_pose
from light_rules import resolve_light_conditions

RNG_SEED = 20260829
ULID_EPOCH = datetime(2026, 8, 29, tzinfo=timezone.utc)
BLURHASH_PENDING = "PENDING-RUN-MAKE-PLACEHOLDERS"

# ---------------------------------------------------------------------------
# Pose concepts. Each category has 30 concepts; each is emitted twice in
# different settings for 60 poses per category. Flags: difficulty override,
# orientation bias, seated (implies seated_variant), partner (maternity),
# pet (family), backlit affinity.


def c(slug, difficulty=None, seated=False, partner=False, pet=False,
      toddler=False, horizontal=False, party=False, elder=False,
      locations=None, daylight=False, solo=False):
    return {
        "slug": slug,
        "locations": locations,  # restrict the setting to where the pose makes sense
        "daylight": daylight,    # no night_flash / blue hour for this concept
        "solo": solo,            # pets: the animal alone, no owner in frame
        "difficulty": difficulty,
        "seated": seated,
        "partner": partner,
        "pet": pet,
        "toddler": toddler,
        "horizontal": horizontal,
        "party": party,    # wedding: the couple with their wedding party
        "elder": elder,    # wedding: one of the couple with a parent
    }


CONCEPTS = {
    "couples": [
        c("walking-hand-in-hand"),
        c("look-back-over-shoulder"),
        c("forehead-lean"),
        c("dip-kiss", difficulty="advanced"),
        c("piggyback", difficulty="moderate"),
        c("spin-out-twirl", difficulty="moderate"),
        c("bench-snuggle", seated=True),
        c("slow-dance"),
        c("jacket-drape"),
        c("nose-to-nose"),
        c("run-and-catch", difficulty="moderate", horizontal=True),
        c("lap-sit-laugh", seated=True),
        c("back-to-back"),
        c("hug-from-behind"),
        c("whispered-secret"),
        c("blanket-wrap", seated=True),
        c("lift-and-spin", difficulty="advanced"),
        c("arm-swing-stroll", horizontal=True),
        c("close-slow-sway"),
        c("chin-lift-kiss"),
        c("wall-lean"),
        c("stair-sit", seated=True),
        c("temple-kiss"),
        c("interlocked-hands-detail"),
        c("silhouette-kiss", horizontal=True),
        c("campfire-cuddle", seated=True),
        c("hand-kiss"),
        c("walk-away-look-back", horizontal=True),
        c("shoulder-lean-sunset", seated=True),
        c("umbrella-share"),
    ],
    "senior": [
        c("letterman-lean"),
        c("curb-sit", seated=True),
        c("over-shoulder-look"),
        c("hands-in-pockets"),
        c("crossed-arms-grin"),
        c("mid-laugh-candid"),
        c("guitar-on-steps", seated=True),
        c("window-light-portrait"),
        c("field-walk", horizontal=True),
        c("skateboard-underarm"),
        c("cap-toss", difficulty="moderate"),
        c("stair-perch", seated=True),
        c("rail-lean"),
        c("jacket-over-shoulder"),
        c("chin-on-hand", seated=True),
        c("walk-toward-camera"),
        c("dress-twirl", difficulty="moderate"),
        c("sunset-silhouette", horizontal=True),
        c("brick-wall-lean"),
        c("cross-legged-sit", seated=True),
        c("hair-flip", difficulty="moderate"),
        c("profile-look-away"),
        c("hands-on-hips"),
        c("denim-jacket-swing"),
        c("bleacher-row", seated=True),
        c("path-stroll", horizontal=True),
        c("laugh-over-shoulder"),
        c("doorway-frame"),
        c("meadow-sit", seated=True),
        c("varsity-jump", difficulty="advanced"),
    ],
    "family": [
        c("group-walk-hold-hands", horizontal=True),
        c("toddler-toss", difficulty="advanced", toddler=True),
        c("sandwich-hug"),
        c("piggyback-parade", difficulty="moderate", horizontal=True),
        c("picnic-blanket", seated=True, horizontal=True),
        c("everyone-look-at-baby", toddler=True),
        c("tickle-pile", difficulty="moderate"),
        c("walk-away-holding-hands", horizontal=True),
        c("swing-the-toddler", difficulty="moderate", toddler=True),
        c("nose-boop-line"),
        c("group-squeeze"),
        c("shoulder-ride"),
        c("race-to-camera", difficulty="moderate", horizontal=True),
        c("tree-line-lean"),
        c("stair-stack", seated=True),
        c("couch-pile", seated=True, horizontal=True),
        c("lift-the-littlest", difficulty="moderate", toddler=True),
        c("ring-around-parents"),
        c("look-at-each-other-laugh"),
        c("dog-in-the-middle", pet=True),
        c("tallest-to-smallest"),
        c("parents-kiss-kids-react"),
        c("jump-together", difficulty="moderate", horizontal=True),
        c("heads-together-huddle"),
        c("bench-row", seated=True, horizontal=True),
        c("path-wander", horizontal=True),
        c("blanket-fort-peek", seated=True),
        c("tailgate-sit", seated=True),
        c("field-run", difficulty="moderate", horizontal=True),
        c("story-time-circle", seated=True),
    ],
    "maternity": [
        c("hands-on-bump-profile"),
        c("partner-behind-hug", partner=True),
        c("window-silhouette"),
        c("dress-flow-field"),
        c("look-down-at-bump"),
        c("partner-kneel-bump-kiss", partner=True, difficulty="moderate"),
        c("armchair-rest", seated=True),
        c("heart-hands-on-bump"),
        c("meadow-walk", horizontal=True),
        c("sheer-curtain-light"),
        c("partner-forehead-touch", partner=True),
        c("rocking-chair", seated=True),
        c("nursery-window"),
        c("robe-drape-studio"),
        c("both-hands-cradle"),
        c("laughing-with-partner", partner=True),
        c("side-light-profile"),
        c("floor-lean", seated=True),
        c("ultrasound-hold"),
        c("partner-hands-heart", partner=True),
        c("backlit-profile-golden"),
        c("garden-barefoot"),
        c("mirror-reflection"),
        c("partner-walk-behind", partner=True, horizontal=True),
        c("chin-up-eyes-closed"),
        c("bump-from-the-side-seated", seated=True),
        c("partner-slow-dance", partner=True),
        c("staircase-gown", difficulty="moderate"),
        c("wildflower-crown"),
        c("partner-bench-lean", partner=True, seated=True),
    ],
    "wedding": [
        c("first-look-reveal"),
        c("veil-wrap-kiss"),
        c("aisle-walk-back", horizontal=True),
        c("first-dance-sway"),
        c("dip-on-the-dance-floor", difficulty="advanced"),
        c("bouquet-between-them"),
        c("ring-hands-detail"),
        c("forehead-to-forehead-vows"),
        c("dress-twirl", difficulty="moderate"),
        c("jacket-over-shoulders"),
        c("sparkler-exit-run", difficulty="moderate", horizontal=True),
        c("bubble-exit-arms-up", difficulty="moderate", horizontal=True),
        c("cake-table-lean"),
        c("champagne-toast"),
        c("stairs-train-spread", seated=True),
        c("getting-ready-tie-fix"),
        c("dress-button-help"),
        c("parent-dance-laugh", elder=True),
        c("wedding-party-walk", party=True, horizontal=True),
        c("wedding-party-huddle-cheer", party=True),
        c("wedding-party-serious-then-laugh", party=True, horizontal=True),
        c("bride-with-party-arms-up", party=True, horizontal=True),
        c("groom-with-party-lean", party=True, horizontal=True),
        c("golden-hour-walk-away", horizontal=True),
        c("night-portrait-under-string-lights"),
        c("hand-on-cheek-look"),
        c("carry-lift-laugh", difficulty="advanced"),
        c("seated-lap-pull-in", seated=True),
        c("window-light-dress-portrait"),
        c("last-dance-alone", horizontal=True),
    ],
    "lifestyle": [
        c("bench-lean-back-boot-up", seated=True),
        c("bench-arm-along-backrest", seated=True, horizontal=True),
        c("mid-stride-walk"),
        c("one-boot-up-grab", difficulty="moderate"),
        c("hand-in-hair-look-up"),
        c("holding-a-book-look-away"),
        c("coffee-cup-lean"),
        c("steps-sit-elbows-on-knees", seated=True),
        c("wall-lean-look-off"),
        c("over-shoulder-look-back"),
        c("hands-in-pockets-squint"),
        c("curb-or-step-sit-legs-out", seated=True),
        c("tuck-hair-mid-turn"),
        c("laugh-at-nothing-off-camera"),
        c("skirt-or-jacket-twirl", difficulty="moderate"),
        c("stand-and-look-at-the-view"),
        c("railing-lean-both-arms", horizontal=True),
        c("doorframe-shoulder-lean"),
        c("crouch-on-heels-camera-low", seated=True),
        c("bag-strap-hold-walk"),
        c("chin-up-eyes-closed-sun"),
        c("sit-on-bench-back-look-over", seated=True),
        c("half-turn-walk-away", horizontal=True),
        c("hand-on-hip-hip-out"),
        c("sunglasses-half-off"),
        c("lean-on-bike-or-car"),
        c("grass-sit-legs-to-side", seated=True),
        c("reflection-check-in-glass"),
        c("stretch-arms-overhead", difficulty="moderate"),
        c("mid-jump-off-step", difficulty="advanced"),
    ],
    "headshots": [
        c("classic-three-quarter-turn", locations=["studio", "home", "urban"]),
        c("arms-crossed-slight-smile", locations=["studio", "urban"]),
        c("lean-on-wall-hands-in-pockets", locations=["urban", "studio"]),
        c("seated-forward-lean-elbows-on-knees", seated=True, locations=["studio", "home"]),
        c("chin-down-eyes-up", locations=["studio", "home"]),
        c("laugh-off-camera-then-back", locations=["studio", "urban", "home"]),
        c("hand-on-chin-thinking", seated=True, locations=["studio", "home"]),
        c("over-shoulder-look-back", locations=["studio", "urban"]),
        c("walking-toward-camera-street", horizontal=True, locations=["urban"], daylight=True),
        c("desk-edge-perch", seated=True, locations=["home", "studio"]),
        c("jacket-button-fix", locations=["studio", "urban"]),
        c("window-light-profile-turn", locations=["home", "studio"]),
        c("stool-sit-one-foot-on-rung", seated=True, locations=["studio"]),
        c("hands-clasped-at-waist", locations=["studio", "home"]),
        c("shoulder-to-camera-head-turned", locations=["studio", "urban"]),
        c("glasses-in-hand", locations=["studio", "home"]),
        c("doorway-lean-arms-loose", locations=["home", "urban"]),
        c("crossed-arms-no-smile-power", locations=["studio", "urban"]),
        c("mid-conversation-gesture", locations=["home", "studio"]),
        c("stairs-sit-forearms-on-thighs", seated=True, locations=["urban", "home"]),
        c("coffee-in-hand-relaxed", locations=["home", "urban"]),
        c("straight-on-soft-smile", locations=["studio"]),
        c("rail-lean-city-behind", horizontal=True, locations=["urban"], daylight=True),
        c("head-tilt-warm-smile", locations=["studio", "home"]),
        c("hands-in-back-pockets", locations=["urban", "studio"]),
        c("sleeve-roll-mid-motion", locations=["studio", "home"]),
        c("seated-turned-to-camera", seated=True, locations=["studio", "home"]),
        c("brick-wall-shoulder-lean", locations=["urban"]),
        c("looking-down-at-notebook", seated=True, locations=["home"]),
        c("full-length-weight-on-back-leg", locations=["studio", "urban"]),
    ],
    "pets": [
        c("sit-together-on-the-grass", seated=True, locations=["field", "forest"]),
        c("walk-on-leash-toward-camera", horizontal=True, locations=["urban", "field", "beach"], daylight=True),
        c("dog-in-lap-on-the-porch-steps", seated=True, locations=["home", "urban"]),
        c("high-five-paw", difficulty="moderate", locations=["field", "urban", "home"]),
        c("forehead-to-forehead-nuzzle", locations=["field", "home", "beach"]),
        c("both-looking-off-same-direction", locations=["field", "beach", "mountain"]),
        c("dog-runs-to-owner", difficulty="advanced", horizontal=True, locations=["field", "beach"], daylight=True),
        c("carry-the-small-dog", locations=["urban", "field", "home"]),
        c("couch-cuddle-window-light", seated=True, locations=["home"]),
        c("treat-held-above-camera", locations=["field", "home", "studio"]),
        c("dog-alone-head-tilt", solo=True, locations=["studio", "home"]),
        c("dog-alone-mid-shake-off", solo=True, difficulty="advanced", locations=["beach", "field"], daylight=True),
        c("dog-alone-sitting-proud-profile", solo=True, locations=["field", "mountain", "beach"]),
        c("dog-alone-lying-chin-on-paws", solo=True, locations=["home", "studio"]),
        c("dog-alone-running-toward-camera", solo=True, difficulty="advanced", horizontal=True, locations=["beach", "field"], daylight=True),
        c("owner-crouched-dog-sitting-beside", locations=["urban", "field", "forest"]),
        c("walk-away-together-down-path", horizontal=True, locations=["forest", "field", "beach"], daylight=True),
        c("dog-on-bench-owner-beside", seated=True, locations=["urban", "field"]),
        c("belly-rub-laugh", seated=True, locations=["field", "home"]),
        c("shake-hands-sit-command", locations=["field", "urban", "studio"]),
        c("lift-the-dog-up-to-face", difficulty="moderate", locations=["field", "beach", "home"]),
        c("lying-in-the-grass-side-by-side", seated=True, horizontal=True, locations=["field"], daylight=True),
        c("dog-looks-up-at-owner", locations=["urban", "field", "home"]),
        c("kiss-on-the-head", locations=["field", "home", "beach"]),
        c("tug-toy-play", difficulty="moderate", locations=["field", "beach", "home"]),
        c("owner-sitting-dog-head-in-lap", seated=True, locations=["field", "home", "beach"]),
        c("both-in-the-doorway", locations=["home"]),
        c("piggyback-the-dog", difficulty="advanced", locations=["field", "beach"], daylight=True),
        c("water-edge-paws-wet", locations=["beach"], daylight=True),
        c("sit-together-against-the-sky", horizontal=True, locations=["field", "beach", "mountain"], daylight=True),
    ],
}

# ---------------------------------------------------------------------------
# Prompt banks. Real direction copy, written per category and tone, in the
# voice of a photographer talking a nervous non-model through a session.

PROMPTS = {
    "couples": {
        "nervous_client": [
            "You don't have to look at the camera. Just look at each other.",
            "There's no wrong way to do this. Stand close and I'll do the rest.",
            "Forget I'm here for a second. Tell them about the first time you met.",
            "If it feels awkward, laugh about it. That's the shot.",
            "You two just talk. I'm not even listening, I promise.",
            "Nobody's judging your hands. Wherever they land is right.",
            "Close your eyes. When you open them, just find each other.",
            "You're doing better than you think. Stay exactly like that.",
            "Don't worry about your smile. Look at them and it'll happen on its own.",
            "We can take this as slow as you want. Start by just holding hands.",
            "If you don't know what to do with your face, kiss their shoulder.",
            "I'll count to three, but nothing happens on three. Just breathe.",
            "Whatever you did just then — that was perfect. Do it again.",
            "You can't mess this up. There are no outtakes, only extras.",
            "It's okay to feel silly. Silly photographs beautifully.",
            "Lean into them the way you do on the couch at home.",
            "Look at their ear. I know it sounds strange. Trust me.",
            "Ignore the camera entirely. It's just a long walk with better light.",
        ],
        "playful": [
            "Walk toward me and whisper something you'd get in trouble for saying.",
            "On three, both of you try to kiss the other's cheek at the same time.",
            "Tell them your worst joke. The one they've heard a hundred times.",
            "Sneak up and steal a kiss before they notice.",
            "Try to make each other laugh without touching. Loser buys dinner.",
            "Give me your best impression of each other.",
            "Swing your arms like you're twelve and just started dating.",
            "Whisper what you're actually having for dinner tonight.",
            "Bump hips on every third step. I'll count.",
            "One of you knows a secret. The other has to get it out.",
            "Grab their hand and don't tell them where you're going.",
            "Show me the face you make when they take too long to get ready.",
            "Squeeze them until they squeak.",
            "Trade jackets. Yes, really. Now act natural.",
            "Do your handshake. Every couple has one — if not, invent it now.",
            "Dance like the song is only in your heads. Because it is.",
            "Race-walk to that tree. Romantically.",
            "Five seconds of tickling starts... now.",
        ],
        "calm": [
            "Drop your shoulders. Take one breath out. Now look back at me.",
            "Stand still together and listen to the wind for a second.",
            "Rest your head on their chest and close your eyes.",
            "Slow everything down by half. Even your blinking.",
            "Take one slow step at a time, like the ground is warm sand.",
            "Hold each other and sway, barely. Less than that. Perfect.",
            "Look out at the horizon together. Don't talk.",
            "Breathe in together — and let it out slow.",
            "Just stand in the light for me. That's the whole job.",
            "Let your hands find each other without looking down.",
            "Settle in like the last minute of a slow song.",
            "Soften your jaw. Now soften it again.",
            "Think about tomorrow morning, nothing else.",
            "Stay quiet together. Quiet looks incredible on you two.",
            "Melt into them a little more with every breath out.",
            "Rest there. There's no clock today.",
        ],
        "romantic": [
            "Press your foreheads together and close your eyes for three seconds.",
            "Kiss them like the car is about to leave.",
            "Slow dance. No music. You know the song.",
            "Hold their face like you're about to say something important.",
            "Come in for the kiss — but stop one inch short and stay there.",
            "Trace their jaw with your thumb, slowly.",
            "Tell them the exact moment you knew.",
            "Wrap them up from behind and kiss just below the ear.",
            "Look at their lips, then their eyes. Take your time.",
            "Pull them in by the waist like you've done it a thousand times.",
            "Whisper your favorite thing about today.",
            "Lift their chin and hold there. Don't kiss yet.",
            "Dip them — slowly, I've got you — and hold.",
            "Kiss their forehead and keep your eyes closed after.",
            "Say their name, just their name, quietly.",
            "Hold both their hands and press them to your chest.",
            "Nose to nose. Now smile without pulling away.",
            "Kiss the back of their hand like it's 1948.",
        ],
    },
    "senior": {
        "nervous_client": [
            "You don't have to smile yet. We're just testing the light on your jacket.",
            "Nobody sees these until you've approved them. Deal?",
            "Look past my shoulder at that sign. See? You're modeling already.",
            "Every senior feels weird for the first ten minutes. You're right on schedule.",
            "Shake out your hands. Now forget I said anything about hands.",
            "You can blink. Blinking is allowed and encouraged.",
            "Give me a fake laugh. Yes, it works every time — see, that one's real.",
            "Look down at your shoes, then up at me on three. No smile needed.",
            "You get veto power on every single frame. You're the editor.",
            "Walk like you're crossing the parking lot at school. That's it.",
            "This isn't picture day. There's no line behind you.",
            "Fix your hair if you want. Honestly, the pause looks great too.",
            "Tell me about your weekend while I sort out my settings.",
            "My job is deleting the bad ones. You'll never see them.",
            "One more just like that. You've officially got the hang of this.",
            "We can start with your 'whatever' face. That one's usually the keeper.",
        ],
        "playful": [
            "Show me your yearbook smile — now show me the real one.",
            "Strut at me like the hallway is yours. Because it is.",
            "Flip the jacket over your shoulder like it's a movie poster.",
            "Give the camera the look you give your little brother.",
            "Spin, and wherever you land, own it.",
            "Laugh at nothing. Full commitment. Sell it.",
            "Pretend you just aced the final you didn't study for.",
            "Hands in pockets, chin up, gum-commercial confidence.",
            "Kick a leaf at me. Gently. This lens was expensive.",
            "Give me main-character-walking-to-the-bus energy.",
            "Do the smallest possible dance move you can get away with.",
            "Look over your shoulder like I owe you money.",
            "Toss your hair like a shampoo ad, then laugh about it.",
            "Jump on three. One... two... wait for it...",
            "Whisper your most controversial cafeteria opinion.",
            "Point at the camera like you just won something.",
        ],
        "calm": [
            "Drop your shoulders and let your arms hang heavy.",
            "Look off toward the water and think about next fall.",
            "Slow your walk to half speed and let your eyes wander.",
            "Lean back on the wall and let it hold all your weight.",
            "Take one deep breath and let your face do nothing at all.",
            "Turn your face until you feel the sun on your cheek. Stop there.",
            "Tuck your hair behind your ear, slow, like you're thinking.",
            "Sit however you'd sit if I weren't here.",
            "Close your eyes. Open them on my count, right into the lens.",
            "Rest your chin on your hand and let your gaze go soft.",
            "Watch the traffic go by like it's a movie.",
            "Let the smile fade slowly. Stop. Right there.",
            "Cross your ankles, lean back, and study the clouds.",
            "Think of the person who makes you laugh most. Don't smile. Try not to.",
        ],
    },
    "family": {
        "nervous_client": [
            "The kids don't have to sit still. Chase them. I'll keep up.",
            "Nobody has to look at the camera. Look at whoever you love most in this group.",
            "If the toddler melts down, we roll with it. Meltdowns are ten percent of my portfolio.",
            "Parents, you're just furniture right now. Comfortable, loving furniture.",
            "There's no wrong way to hold your kid. They'll show you where they fit.",
            "Messy hair stays in. It's proof you all actually live together.",
            "Whisper-count to three, then everybody squeeze whoever's closest.",
            "Mom, Dad — look at each other. The kids will do the rest, I promise.",
            "You handle the hugging. I'll handle the timing.",
            "We'll get one photo where everyone smiles at me for Grandma. Then we play. Deal?",
            "If someone cries, we take a snack break. That includes the adults.",
            "Just walk. Kids wander. That's the picture.",
            "You don't need to fix his collar. I promise it reads as charming.",
            "Everyone squish in until somebody giggles.",
            "Pretend it's Sunday morning and nobody has anywhere to be.",
            "The dog is in charge now. Everyone watch the dog.",
        ],
        "playful": [
            "Everybody tickle the person on your left. Go.",
            "Group hug, but it's a competition. Tightest squeeze wins.",
            "Kids, on three, show me your silliest monster face. Parents, act terrified.",
            "Whoever laughs first has to do the dishes tonight.",
            "Parents kiss. Kids, say ewww as loud as legally allowed.",
            "Race to that tree and back. Parents, you're allowed to lose.",
            "Give the little one a countdown, then launch. I'll be ready.",
            "Everyone jump on three — Dad, you especially.",
            "Kids, bury Dad's feet. Dad, act like you haven't noticed.",
            "Pile on the parents like a snow drift.",
            "Ring around the parents — nobody falls down until I say.",
            "Loudest family cheer on three. Neighbors optional.",
            "Tallest to smallest, then everyone lean out and peek at me.",
            "Swing the little one on every third step — one, two, wheee.",
            "Thumb war tournament. I'll photograph the drama.",
            "Everyone point at the person most likely to steal dessert.",
        ],
        "calm": [
            "Everyone find somewhere comfortable to lean on someone else.",
            "Look at the little one and just watch them for a minute.",
            "Walk slow, hold hands, let the kids set the pace.",
            "Squeeze in close and take one big family breath together.",
            "Parents, rest your heads together while the kids settle in.",
            "Cuddle up like it's the end of movie night.",
            "Watch the water together. Nobody needs to say anything.",
            "Tuck the smallest one into the middle and let them get cozy.",
            "Sway together, slow, like the world's gentlest huddle.",
            "Everybody close your eyes except the baby. They can supervise.",
            "Read them one page. I just want the listening faces.",
            "Sit close and watch the sky change. That's the whole plan.",
            "Let the kids get heavy in your arms. Heavy is the picture.",
            "One quiet minute, all together. I'll take care of the rest.",
        ],
    },
    "maternity": {
        "nervous_client": [
            "There's no pose. Just stand with your baby for a minute — you're already holding them.",
            "You can't do this wrong. Your hands already know where to go.",
            "If you feel silly, look down at the bump. Nobody's watching your face then.",
            "We'll go at whatever speed your feet are happy with today.",
            "You don't have to arch or bend anything. Stand comfortable and I'll move instead.",
            "Eyes closed is always allowed. Some of my favorite frames are eyes closed.",
            "Any time you need to sit, we make sitting the pose.",
            "The dress does most of the work. You just breathe.",
            "Nobody expects you to feel graceful at eight months. You look it anyway.",
            "Tell me if anything aches and we swap to the next idea, no ceremony.",
            "Look at your hands, not the lens. Your hands are the story.",
            "It's just us out here. Take up all the space you want.",
            "You've been posing for this one for months. This is the easy part.",
            "If laughing feels better than smiling, laugh.",
            "Rest your weight on your back foot. Comfort photographs beautifully.",
            "We can stop for water and snacks any time. Growing a person is cardio.",
        ],
        "calm": [
            "Close your eyes and take the slowest breath you've had all week.",
            "Rest both hands on the bump and just listen.",
            "Turn toward the window until the light warms your cheek.",
            "Sway side to side, barely, like you're already rocking them.",
            "Look down and let your shoulders finally let go.",
            "Trace one slow circle with your thumb.",
            "Let the dress settle. Then one more quiet breath.",
            "Chin up, eyes closed, and let the sun do its thing.",
            "Think about the first morning home. Stay there a while.",
            "Hold still in the breeze and let everything else move.",
            "Soften your hands. They don't need to grip, just rest.",
            "One breath for you, one for the baby. Repeat.",
            "Settle into the chair like the afternoon has no plans.",
            "Hum something. Whatever they've been kicking along to.",
            "Let your eyes go soft and heavy, almost sleepy. Stay.",
            "Cradle the bottom of the bump like you're weighing something precious.",
        ],
        "romantic": [
            "Wrap your arms around from behind and rest your hands over theirs.",
            "Kneel down and kiss the bump like you're telling them a secret.",
            "Press your foreheads together with the baby right between you.",
            "Whisper the name you haven't told anyone else yet.",
            "Look at her like she's doing the most impressive thing you've ever seen. She is.",
            "Slow dance, all three of you.",
            "Both of you, hands on the bump, and wait for a kick.",
            "Kiss her temple and keep your eyes on the bump.",
            "Tell her one thing you can't wait to watch her do as a mom.",
            "Hold her hand against your chest and just stand in it.",
            "Tuck her under your arm like the weather's turning.",
            "Make a heart with your hands together, right where the baby is.",
        ],
        "playful": [
            "Show me the bump like it just won first prize. It did.",
            "Tell the baby the family gossip. They can keep a secret.",
            "Give the bump a drumroll — gently, they're napping.",
            "Laugh at how the wind keeps choosing your hair over the dress.",
            "Practice your 'we're not naming them that' face.",
            "Ask the baby to kick on three. They won't, but the waiting face is gold.",
            "Waddle at me like it's a runway. Own the waddle.",
            "Tell me the weirdest craving. Act it out. No words.",
            "Point at the bump and mouth 'this was your idea.'",
            "Whisper 'you can be anything except a drummer' to the bump.",
            "Balance your lemonade on the bump — kidding, but that laugh was real.",
            "Introduce the bump to the camera. Full name optional.",
        ],
    },
    "wedding": {
        "nervous_client": [
            "Forget the schedule for two minutes. It's just the two of you and me.",
            "You don't have to perform anything. Stand close and breathe.",
            "The dress is already doing half the work. You just have to hold hands.",
            "Nobody's watching this part. The guests are at the bar.",
            "Whisper what you're most excited about tonight. I won't hear it.",
            "If your hands feel weird, hold each other's. Problem solved.",
            "You can look at me or at each other. Both are right.",
            "Blink, breathe, fix the veil if you want. I'll wait.",
            "You've already done the hard part. This is the easy part.",
            "Say something only the two of you would find funny.",
            "Lean in a little. A little more. That's exactly it.",
            "The tie is fine. The hair is fine. You're fine.",
            "Take one slow breath together. Now look up.",
            "I'll tell you when to look at me. Until then, ignore me completely.",
            "If it feels like a lot, laugh at me. That's my favourite frame.",
            "Just walk toward me like you're late to your own cocktail hour.",
        ],
        "playful": [
            "Ring check! Show me the hands like you're bragging.",
            "Who cried first at the ceremony? Point at them.",
            "Spin the dress like you paid for the whole thing. You did.",
            "Give me your best 'we did it' face on three.",
            "Cheers with nothing in your hands. Sell it.",
            "Pretend I'm the DJ and I just played your guilty pleasure song.",
            "Whoever leads the dance is wrong. Fight about it silently.",
            "Bridal party: react like the bride just told the best secret.",
            "Groomsmen: too cool for photos. Now break and lose it.",
            "Sneak a kiss like the officiant hasn't said you can yet.",
            "Bouquet up like a trophy. Groom, be the confetti.",
            "Run at me like the bar is behind me. It is.",
            "Tell each other the worst dad joke you know. Winner gets cake.",
            "Everyone hands in the air like the bubbles are winning.",
            "Whisper your table number in a very sexy voice.",
            "Cake face threat. Don't do it. Just threaten.",
        ],
        "calm": [
            "Stand together and look out over the room. Take it in.",
            "Rest your head on their shoulder and just watch the lights.",
            "Slow dance without music. Just sway.",
            "Hold hands and walk. I'll follow behind you.",
            "Fix each other's collar, sleeve, hair. Slowly.",
            "Close your eyes. Feel their hand. Open when you're ready.",
            "Sit on the step and let the dress fall where it falls.",
            "Turn away from me and look at the same thing together.",
            "Forehead to forehead. Breathe out at the same time.",
            "Hold the bouquet low between you and just stand there.",
            "Look down at your rings, then up at each other.",
            "Stand in the doorway and let the light do the rest.",
            "One hand on their chest. Feel that? Good. Stay.",
            "Walk away from me down the path. Don't look back yet.",
        ],
        "romantic": [
            "Kiss like the first dance just ended.",
            "Pull the veil around both of you like a tent for two.",
            "Say your favourite line from the vows again, quietly.",
            "Lift their chin and wait. Let them come to you.",
            "Hold their face like you're memorising it.",
            "Dance like the last song is playing and nobody's left.",
            "Hug like you just got told the good news.",
            "Nose to nose. Don't kiss yet. Wait for it.",
            "Wrap your arms around from behind and rest your chin on their shoulder.",
            "Look at each other like you did at the end of the aisle.",
            "Slow kiss on the forehead. Hold it.",
            "Sway, then dip them slowly. Bring them back up laughing.",
        ],
    },
    "lifestyle": {
        "nervous_client": [
            "Sit here. Take it just like this. That's the whole job.",
            "You don't have to look at me. Look at the mountains. They're less judgy.",
            "Is this pose bad or cute? Trust me, it's cute. Hold it.",
            "Nobody sees these until you say so. You're the editor.",
            "Walk toward me like you're going to get coffee. That's it, that's the shot.",
            "Fix your hair if you want. Actually, keep fixing it. That's the frame.",
            "It's just me and a very quiet camera. Take a breath.",
            "Pretend I'm your friend who's terrible at photos. Lower the bar.",
            "Lean on the wall like you're waiting for someone who's late.",
            "You can blink, you can move, you can laugh at me. All of it works.",
            "Look at your shoes, then look up on three. No smile required.",
            "Hold the book like you actually want to read it. Ignore me.",
            "Give me a fake laugh. See? Now it's a real one. Keep going.",
            "There's no wrong way to sit on a bench. Prove me right.",
            "We'll take ten of these and you'll like two. That's normal.",
            "Tell me about your weekend while I fiddle with settings.",
        ],
        "playful": [
            "Main character energy. The street is your runway.",
            "Look over your shoulder like I owe you money.",
            "Twirl, and wherever you land, own it.",
            "Kick one boot up and grab it. Very Sound of Music.",
            "Sunglasses halfway off, like you just spotted a celebrity.",
            "Laugh at nothing over there. Full commitment.",
            "Squint at the horizon like you're in a perfume ad.",
            "Walk away from me, then turn back like you forgot to say something.",
            "Give me 'just landed, no plans' energy.",
            "Sit on the curb like you're waiting for the bus in a music video.",
            "Hair flip. Then laugh at the fact that you just did a hair flip.",
            "Point at something off-frame like it's the best thing you've ever seen.",
            "Stretch like you just woke up somewhere gorgeous.",
            "Jump off the step on three. Land like it was easy.",
            "Coffee up, chin up, tiny smirk. Very morning-influencer.",
            "Hip out, hand on hip, dare the camera to say something.",
        ],
        "calm": [
            "Lean back, arm along the bench, and just watch the view.",
            "Tuck your hair behind your ear, slow, like you're thinking.",
            "Close your eyes, chin up, find the sun on your face. Stop there.",
            "Slow your walk to half speed and let your eyes wander.",
            "Hold the book against you and look off down the street.",
            "Lean on the railing with both arms and let your shoulders drop.",
            "Sit however you'd sit if I weren't here.",
            "Look past my shoulder at the hills. Let the smile fade slowly.",
            "Rest your head on your hand and let your gaze go soft.",
            "Crouch down, elbows on knees, look straight at me without smiling.",
            "Hands in pockets, weight on one leg, breathe out.",
            "Turn your face until you feel the light on your cheek. Hold it.",
            "Sit in the grass, legs to the side, and look at nothing in particular.",
            "Half turn away from me. Keep your face in profile. Stay.",
        ],
    },
    "headshots": {
        "nervous_client": [
            "Nobody likes headshots. You're doing better than the last twelve people.",
            "You get veto power on every frame. Delete anything you hate.",
            "Look at the lens like it's someone you like but don't need to impress.",
            "Chin down a touch. That's it. That's the whole trick.",
            "Fake laugh for me. See, now that one's real. Hold that face.",
            "We'll do a boring one first to get it out of the way.",
            "Shake out your shoulders. Now forget I mentioned shoulders.",
            "Say something rude about my lighting. Great, that's the smile.",
            "Blink whenever you want. I'll wait.",
            "Turn your body away from me, then bring just your face back.",
            "It's a headshot, not a passport. You're allowed to look friendly.",
            "Tell me what you actually do all day while I fix the light.",
            "Take a breath out. Right at the bottom of it, look at me.",
            "Nobody sees these until you've picked your favourite.",
            "That's the one. Give me three more just like it.",
            "Fix your collar if you want. The fixing looks great too.",
        ],
        "playful": [
            "Give me the smile you use when you're winning an argument.",
            "Look at the camera like it just gave you a promotion.",
            "Arms crossed, slight smirk. CEO of something.",
            "Pretend I'm your favourite coworker. Not the one you're thinking of.",
            "Laugh at my joke. I haven't told one yet. Do it anyway.",
            "Big smile, then let it shrink to the one you'd use in a meeting.",
            "Look over your shoulder like I said your name from across the office.",
            "Hands in pockets, lean on the wall, LinkedIn but make it fun.",
            "Think of your most embarrassing email. Now smile through it.",
            "Give me 'I know something you don't' energy.",
            "Point at me like you've just spotted me across a room.",
            "Roll a sleeve. Slowly. Very capable.",
            "Do the head tilt. Now do half of that.",
            "Walk at me like the meeting just ended early.",
        ],
        "calm": [
            "Drop your shoulders and let your face go still.",
            "Look just past the lens, then come to it slowly.",
            "Rest your elbows on your knees and look up at me.",
            "Turn to the window and let the light find your cheek.",
            "Hands loose at your sides. Weight on your back foot.",
            "Close your eyes. Open them on three, straight into the lens.",
            "Let the smile fade until it's barely there. Stop.",
            "Sit tall, then let your spine relax by ten percent.",
            "Think about the last thing that went right this week.",
            "Chin toward me, eyes soft, no smile needed.",
            "Hold your glasses and just look at me like we're talking.",
            "Breathe out and hold the end of the breath.",
            "Lean into the doorframe and let it take your weight.",
            "Look down at the page, then up at me when you're ready.",
        ],
    },
    "pets": {
        "nervous_client": [
            "The dog doesn't have to sit still. That's my problem, not yours.",
            "Talk to your dog like I'm not here. That's the whole session.",
            "If they wander off, we follow. Nothing here is a mistake.",
            "You just look at the dog. I'll worry about the dog looking at me.",
            "Hold the treat by my lens. Hold it. Hold it. Perfect.",
            "Nobody expects a dog to pose. We're catching, not staging.",
            "Scratch behind the ears and keep your face right there.",
            "Say their name in the silly voice. Yes, that one.",
            "Sit however you'd sit on your own couch.",
            "We'll take fifty. Three will be perfect. That's the deal.",
            "If they lick your face, that's the photo. Don't pull away.",
            "Squeaky toy on three. Look at me, not the toy.",
            "Crouch down to their level. Everything gets better down here.",
            "Let them lean on you. Let yourself lean back.",
            "Loose leash, slow walk, ignore me completely.",
            "The messy ones are the good ones. Trust me.",
        ],
        "playful": [
            "Ask for a high five. Sell it like it's the first time.",
            "Run away from them. See who wins.",
            "Belly rub. Full commitment. Laugh at whatever happens.",
            "Lift them up to your face and tell them they're a good dog.",
            "Whisper a secret in the dog's ear.",
            "Both of you look at that squirrel. There's always a squirrel.",
            "Tug of war. I'll photograph the drama.",
            "Piggyback if they'll allow it. If not, we saw nothing.",
            "Kiss the top of their head and don't come back up until I say.",
            "Make the noise that makes their ears go up.",
            "Pretend the dog told a joke.",
            "Walk away together like the credits are rolling.",
            "Hold the treat up high and give me your best begging face too.",
            "Shake hands like you're closing a deal.",
        ],
        "calm": [
            "Sit in the grass and let them settle against you.",
            "Rest your hand on their back and look at the same thing they're looking at.",
            "Forehead to forehead. Breathe slow. Wait for them.",
            "Let their head rest in your lap and just look down at them.",
            "Walk slowly down the path and don't look back.",
            "Sit on the step and let the dog decide where to be.",
            "Stroke from head to tail, slow, and watch their eyes close.",
            "Look off toward the light together.",
            "Lie down beside them and see who falls asleep first.",
            "Let the leash go slack and just stand together.",
            "Hold their paw gently and look at me.",
            "Both of you in the doorway, waiting for nothing.",
            "Stand at the water's edge and let them sniff.",
            "Scratch under the chin until they lean in. Stay there.",
        ],
    },
}

# ---------------------------------------------------------------------------
# Distributions

OUTDOOR_LIGHT_WEIGHTS = [
    ("golden", 34),
    ("open_shade", 16),
    ("overcast", 16),
    ("backlit", 12),
    ("harsh_overhead", 8),
    ("blue", 8),
    ("night_flash", 6),
]
INDOOR_LIGHT_WEIGHTS = [
    ("indoor_window", 72),
    ("night_flash", 16),
    ("backlit", 12),
]

LOCATION_WEIGHTS = {
    "couples": [
        ("field", 22), ("beach", 18), ("urban", 18), ("forest", 14),
        ("mountain", 10), ("home", 8), ("studio", 6),
    ],
    "senior": [
        ("urban", 34), ("field", 18), ("studio", 14), ("beach", 12),
        ("forest", 10), ("home", 6), ("mountain", 6),
    ],
    "family": [
        ("field", 24), ("beach", 20), ("home", 18), ("forest", 14),
        ("urban", 12), ("mountain", 8), ("studio", 4),
    ],
    "maternity": [
        ("studio", 24), ("home", 22), ("field", 20), ("beach", 12),
        ("forest", 12), ("urban", 6), ("mountain", 4),
    ],
    # Venue grounds read as field/urban; getting-ready and reception rooms as home.
    "wedding": [
        ("field", 26), ("urban", 22), ("home", 18), ("forest", 12),
        ("beach", 10), ("mountain", 8), ("studio", 4),
    ],
    # Instagram-style solo shoots: streets, hillside overlooks, benches.
    "lifestyle": [
        ("urban", 34), ("field", 20), ("mountain", 16), ("beach", 12),
        ("forest", 8), ("home", 6), ("studio", 4),
    ],
    # Concepts in these categories restrict their own locations; the weights
    # only order the choice among what a concept allows.
    "headshots": [
        ("studio", 44), ("urban", 28), ("home", 28),
    ],
    "pets": [
        ("field", 30), ("beach", 18), ("home", 18), ("urban", 14),
        ("forest", 10), ("mountain", 6), ("studio", 4),
    ],
}

GEAR_KITS = {
    "couples": [([50, 85], "f/1.8"), ([35, 50], "f/2"), ([85, 135], "f/2"), ([70, 200], "f/2.8")],
    "senior": [([85, 135], "f/1.8"), ([50, 85], "f/2"), ([35, 50], "f/2.8"), ([70, 200], "f/2.8")],
    "family": [([35, 50], "f/4"), ([24, 35], "f/4"), ([50, 85], "f/2.8"), ([35, 70], "f/3.2")],
    "maternity": [([50, 85], "f/2"), ([85, 135], "f/2.8"), ([35, 50], "f/2.8"), ([24, 35], "f/4")],
    "wedding": [([35, 50], "f/2"), ([50, 85], "f/1.8"), ([85, 135], "f/2"), ([24, 70], "f/2.8")],
    "lifestyle": [([35, 50], "f/2"), ([50, 85], "f/1.8"), ([24, 35], "f/2.8"), ([85, 135], "f/2")],
    "headshots": [([85, 135], "f/2.8"), ([50, 85], "f/2.8"), ([85, 135], "f/4"), ([70, 200], "f/2.8")],
    "pets": [([35, 50], "f/2.8"), ([50, 85], "f/2.8"), ([70, 200], "f/2.8"), ([24, 35], "f/4")],
}

INDOOR_LOCATIONS = {"studio", "home"}

# Prompt lines that only make sense when a given subject type is in the pose.
PROMPT_REQUIRES = {
    "If the toddler melts down, we roll with it. Meltdowns are ten percent of my portfolio.": "toddler",
    "Everybody close your eyes except the baby. They can supervise.": "toddler",
    "The dog is in charge now. Everyone watch the dog.": "pet",
}


def weighted(rng: Random, pairs):
    items = [i for i, w in pairs if w > 0]
    weights = [w for _, w in pairs if w > 0]
    return rng.choices(items, weights=weights, k=1)[0]


class Dealer:
    """Deals lines from a bank, reshuffling when exhausted, so reuse is
    spread evenly instead of clustering on the first few lines."""

    def __init__(self, rng: Random, lines: list[str]):
        self.rng = rng
        self.lines = lines
        self.deck: list[str] = []

    def deal(self, subject_types=None) -> str:
        def ok(line):
            req = PROMPT_REQUIRES.get(line)
            return req is None or subject_types is None or req in subject_types

        for attempt in range(2):
            for i in range(len(self.deck) - 1, -1, -1):
                if ok(self.deck[i]):
                    return self.deck.pop(i)
            self.deck = self.lines[:]
            self.rng.shuffle(self.deck)
        raise RuntimeError("no prompt line satisfies the subject constraint")


# Drawn light tags are normalized to the grouped light rules (one solar
# band, one sky tag, two modifiers) by light_rules.resolve_light_conditions.
# Resolution consumes no RNG, so pose ULIDs are unaffected and a re-seed
# reproduces exactly what tools/retag_light_groups.py applied to the
# existing catalog.


def build_pose(rng: Random, category: str, concept: dict, slug: str,
               engagement: bool, dealers) -> dict:
    seated = concept["seated"]
    partner = concept["partner"]

    # Subjects
    if category in ("couples",):
        subject_count, subject_types = 2, ["adult"]
    elif category == "wedding":
        if concept["party"]:
            subject_count = weighted(rng, [(4, 30), (5, 30), (6, 25), (7, 15)])
            subject_types = ["adult"]
        elif concept["elder"]:
            subject_count, subject_types = 2, ["adult", "senior_adult"]
        else:
            subject_count, subject_types = 2, ["adult"]
    elif category == "senior":
        subject_count, subject_types = 1, ["teen"]
    elif category in ("lifestyle", "headshots"):
        subject_count, subject_types = 1, ["adult"]
    elif category == "pets":
        if concept["solo"]:
            subject_count, subject_types = 1, ["pet"]
        else:
            subject_count, subject_types = 2, ["adult", "pet"]
    elif category == "maternity":
        if partner:
            subject_count, subject_types = 2, ["pregnant", "adult"]
        else:
            subject_count, subject_types = 1, ["pregnant"]
    else:  # family
        subject_count = weighted(rng, [(3, 24), (4, 34), (5, 24), (6, 14), (7, 4)])
        subject_types = ["adult"]
        kid_pool = ["child", "teen", "toddler"]
        rng.shuffle(kid_pool)
        n_kid_types = min(subject_count - 1, weighted(rng, [(1, 40), (2, 40), (3, 20)]))
        subject_types += kid_pool[:n_kid_types]
        if concept["toddler"] and "toddler" not in subject_types:
            subject_types = ["adult", "toddler"] + [
                t for t in subject_types if t not in ("adult", "toddler")
            ]
            subject_types = subject_types[:subject_count]
        if concept["pet"] or (rng.random() < 0.10 and len(subject_types) < subject_count):
            if "pet" not in subject_types:
                subject_types.append("pet")
        if rng.random() < 0.08 and len(subject_types) < subject_count:
            subject_types.append("senior_adult")
        subject_types = subject_types[:subject_count]

    # Location and light
    allowed = concept.get("locations")
    pool = [(loc, w) for loc, w in LOCATION_WEIGHTS[category] if not allowed or loc in allowed]
    location = weighted(rng, pool)
    locations = [location]
    light_pairs = INDOOR_LIGHT_WEIGHTS if location in INDOOR_LOCATIONS else OUTDOOR_LIGHT_WEIGHTS
    # Headshots and pet sessions are never shot with on-camera flash at night
    # or in blue hour; a concept can also opt out with daylight=True.
    if concept.get("daylight") or category in ("headshots", "pets"):
        light_pairs = [(l, w) for l, w in light_pairs if l not in ("night_flash", "blue")]
    lights = [weighted(rng, light_pairs)]
    if rng.random() < 0.30:
        extra = weighted(rng, light_pairs)
        if extra not in lights:
            lights.append(extra)

    # Difficulty: concept override, else easy-skewed
    difficulty = concept["difficulty"] or weighted(
        rng, [("easy", 60), ("moderate", 32), ("advanced", 8)]
    )

    orientation = "horizontal" if concept["horizontal"] and rng.random() < 0.75 else "vertical"

    focal, aperture = rng.choice(GEAR_KITS[category])
    needs_reflector = rng.random() < (0.45 if ("backlit" in lights or "indoor_window" in lights) else 0.12)

    # Accessibility
    accessibility = []
    if seated:
        accessibility.append("seated_variant")
    elif rng.random() < 0.08:
        accessibility.append("seated_variant")
    if rng.random() < 0.11:
        accessibility.append("plus_size_flattering")
    if "seated_variant" in accessibility and rng.random() < 0.18:
        accessibility.append("wheelchair")
    if rng.random() < 0.05:
        accessibility.append("limited_mobility")
    if category == "maternity":
        if difficulty == "easy" and (seated or rng.random() < 0.45):
            accessibility.append("late_term")

    # Prompts: nervous_client plus two other distinct tones
    banks = dealers[category]
    if category == "maternity":
        others = ["romantic", rng.choice(["calm", "playful"])] if partner \
            else rng.sample(["calm", "playful"], 2)
    elif category == "couples":
        others = rng.sample(["playful", "calm", "romantic"], 2)
    elif category == "wedding":
        others = rng.sample(["playful", "calm"], 2) if (concept["party"] or concept["elder"]) \
            else rng.sample(["playful", "calm", "romantic"], 2)
    else:
        others = rng.sample(["playful", "calm"], 2)
    tones = ["nervous_client"] + others
    prompts = [{"text": banks[t].deal(subject_types), "tone": t} for t in tones]

    categories = [category] + (["engagement"] if engagement else [])

    return {
        "slug": slug,
        "image": {"thumb": "thumb.jpg", "detail": "detail.jpg", "blurhash": BLURHASH_PENDING},
        "placeholder": True,
        "categories": categories,
        "subject_count": subject_count,
        "subject_types": subject_types,
        "light_conditions": resolve_light_conditions(lights, locations, slug),
        "location_types": locations,
        "orientation": orientation,
        "difficulty": difficulty,
        "prompts": prompts,
        "gear": {"focal_mm": list(focal), "aperture": aperture, "needs_reflector": needs_reflector},
        "accessibility": accessibility,
        "version": 1,
        "status": "active",
    }


def deterministic_ulid(rng: Random, index: int) -> str:
    ts = int(ULID_EPOCH.timestamp() * 1000) + index  # monotonic, sortable
    return str(ULID.from_bytes(ts.to_bytes(6, "big") + rng.randbytes(10)))


# Categories added after the original 240-pose seed shipped. Each gets its
# own RNG seed and ULID epoch so it can be appended to a live catalog
# without deleting or renumbering anything already published.
APPENDED = {
    "wedding": {"seed": 20260911, "epoch": datetime(2026, 9, 11, tzinfo=timezone.utc)},
    "lifestyle": {"seed": 20260914, "epoch": datetime(2026, 9, 14, tzinfo=timezone.utc)},
    "headshots": {"seed": 20260915, "epoch": datetime(2026, 9, 15, tzinfo=timezone.utc)},
    "pets": {"seed": 20260916, "epoch": datetime(2026, 9, 16, tzinfo=timezone.utc)},
}


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--append":
        return append_category(sys.argv[2])
    if len(sys.argv) > 1:
        sys.exit("usage: generate_seed.py [--append <category>]")

    rng = Random(RNG_SEED)

    # Clear previous placeholder poses; refuse to touch real ones.
    kept = []
    for pose_dir in sorted(POSES_DIR.iterdir()) if POSES_DIR.is_dir() else []:
        if not pose_dir.is_dir():
            continue
        try:
            if load_pose(pose_dir).get("placeholder") is False:
                kept.append(pose_dir.name)
                continue
        except Exception:
            pass  # unreadable placeholder debris; regenerate over it
        shutil.rmtree(pose_dir)
    if kept:
        print(f"Kept {len(kept)} non-placeholder poses: {', '.join(kept)}")

    dealers = {
        cat: {tone: Dealer(rng, lines) for tone, lines in banks.items()}
        for cat, banks in PROMPTS.items()
    }

    written = 0
    used_slugs: set[str] = set()
    for category in ("couples", "senior", "family", "maternity"):
        concepts = CONCEPTS[category]
        for i in range(60):
            concept = concepts[i % len(concepts)]
            # ~1/3 of couples poses also carry engagement
            engagement = category == "couples" and i % 3 == 0
            pose = build_pose(rng, category, concept, concept["slug"], engagement, dealers)
            # Concepts repeat twice per category; disambiguate the slug with
            # the setting so every slug is unique and still reads naturally.
            slug = concept["slug"]
            if slug in used_slugs:
                slug = f"{slug}-{pose['location_types'][0]}"
            n = 2
            base = slug
            while slug in used_slugs:
                slug = f"{base}-{n}"
                n += 1
            used_slugs.add(slug)
            pose["slug"] = slug

            pose_id = deterministic_ulid(rng, written)
            pose = {"id": pose_id, **pose}
            pose_dir = POSES_DIR / pose_id
            pose_dir.mkdir(parents=True)
            (pose_dir / "pose.yaml").write_text(
                yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88)
            )
            written += 1

    print(f"Wrote {written} poses to {POSES_DIR}/")
    print("Next: run tools/make_placeholders.py to generate images and blurhashes.")
    return 0


def append_category(category: str) -> int:
    """Add 60 poses for one appended category, touching nothing else.

    Refuses to run if the category already has poses on disk, so a re-run
    cannot duplicate it; the output is deterministic for the category.
    """
    if category not in APPENDED:
        sys.exit(f"error: {category!r} is not an appendable category "
                 f"({', '.join(APPENDED)})")
    dirs = [d for d in sorted(POSES_DIR.iterdir()) if d.is_dir()]
    existing = [d.name for d in dirs if category in (load_pose(d).get("categories") or [])]
    if existing:
        sys.exit(f"error: {len(existing)} {category} poses already exist; refusing to append.")
    used_slugs = {load_pose(d)["slug"] for d in dirs}

    rng = Random(APPENDED[category]["seed"])
    epoch_ms = int(APPENDED[category]["epoch"].timestamp() * 1000)
    dealers = {category: {tone: Dealer(rng, lines)
                          for tone, lines in PROMPTS[category].items()}}
    concepts = CONCEPTS[category]
    written = 0
    for i in range(60):
        concept = concepts[i % len(concepts)]
        pose = build_pose(rng, category, concept, concept["slug"], False, dealers)
        slug = concept["slug"]
        if slug in used_slugs:
            slug = f"{slug}-{pose['location_types'][0]}"
        n = 2
        base = slug
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        pose["slug"] = slug
        ts = epoch_ms + written
        pose_id = str(ULID.from_bytes(ts.to_bytes(6, "big") + rng.randbytes(10)))
        pose = {"id": pose_id, **pose}
        pose_dir = POSES_DIR / pose_id
        pose_dir.mkdir(parents=True)
        (pose_dir / "pose.yaml").write_text(
            yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88))
        written += 1
    print(f"Appended {written} {category} poses to {POSES_DIR}/")
    print("Next: add their ids to dist/ai_subset.json and run make ai-generate CONFIRM=1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
