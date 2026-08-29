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

RNG_SEED = 20260829
ULID_EPOCH = datetime(2026, 8, 29, tzinfo=timezone.utc)
BLURHASH_PENDING = "PENDING-RUN-MAKE-PLACEHOLDERS"

# ---------------------------------------------------------------------------
# Pose concepts. Each category has 30 concepts; each is emitted twice in
# different settings for 60 poses per category. Flags: difficulty override,
# orientation bias, seated (implies seated_variant), partner (maternity),
# pet (family), backlit affinity.


def c(slug, difficulty=None, seated=False, partner=False, pet=False,
      toddler=False, horizontal=False):
    return {
        "slug": slug,
        "difficulty": difficulty,
        "seated": seated,
        "partner": partner,
        "pet": pet,
        "toddler": toddler,
        "horizontal": horizontal,
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
}

GEAR_KITS = {
    "couples": [([50, 85], "f/1.8"), ([35, 50], "f/2"), ([85, 135], "f/2"), ([70, 200], "f/2.8")],
    "senior": [([85, 135], "f/1.8"), ([50, 85], "f/2"), ([35, 50], "f/2.8"), ([70, 200], "f/2.8")],
    "family": [([35, 50], "f/4"), ([24, 35], "f/4"), ([50, 85], "f/2.8"), ([35, 70], "f/3.2")],
    "maternity": [([50, 85], "f/2"), ([85, 135], "f/2.8"), ([35, 50], "f/2.8"), ([24, 35], "f/4")],
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


def with_daylight_bands(lights: list[str], locations: list[str]) -> list[str]:
    """Add the outdoor daylight bands implied by the tags already chosen.

    `soft_low` (6-20 deg) and `mid` (20-45 deg) exist so the app's sun chip
    has poses to offer between mid-morning and mid-afternoon. They are
    derived, not drawn: this consumes no RNG, so pose ULIDs are unaffected
    and a re-seed reproduces exactly what tools/migrate_add_daylight_bands.py
    applied to the existing catalog.
    """
    if all(loc in INDOOR_LOCATIONS for loc in locations):
        return lights
    out = list(lights)
    if {"golden"} & set(lights) and "soft_low" not in out:
        out.append("soft_low")
    if {"open_shade", "overcast", "harsh_overhead"} & set(lights) and "mid" not in out:
        out.append("mid")
    return out


def build_pose(rng: Random, category: str, concept: dict, slug: str,
               engagement: bool, dealers) -> dict:
    seated = concept["seated"]
    partner = concept["partner"]

    # Subjects
    if category in ("couples",):
        subject_count, subject_types = 2, ["adult"]
    elif category == "senior":
        subject_count, subject_types = 1, ["teen"]
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
    location = weighted(rng, LOCATION_WEIGHTS[category])
    locations = [location]
    light_pairs = INDOOR_LIGHT_WEIGHTS if location in INDOOR_LOCATIONS else OUTDOOR_LIGHT_WEIGHTS
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
        "light_conditions": with_daylight_bands(lights, locations),
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


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
