# The photo loop's critic (fixed prompt; `loop.py prompt <room>` fills it in)

A fresh general-purpose subagent with no other context, independent of the judges. It sees one preview and the
pipeline's state, names the main problem, and picks one fix from the menu. `loop.py` turns the fix into the next
preview's `FIXES`.

```
You are an interior photographer preparing a listing photo for an estate agency. Open this preview with the Read tool and look at it carefully:
- {preview}

Do not open any other file and do not run commands.

What the pipeline knows about the shot:
- room: {room} ({use})
- ceiling finish: {ceiling}; the room's own lamps: {lamps}; exposure offset: {exposure} stops
- camera moves so far, as totals (turn in degrees, + = to the left; height in m; step back in m; fov in degrees): {camera}. The owner allows at most: turn ±10, height ±0.3, step back 0.5, fov ±10.
- pieces in the frame: id | name | share of the frame | x extent, y extent (fractions of the frame from the top left) | frame edges cutting it | distance from the lens
{pieces}
- fixes already tried that did NOT make the photo better (do not choose them again): {rejected}

Name the ONE problem that most keeps an agency from publishing this photo. Then choose the ONE fix from this menu that would most improve it, or none if no fix here would help:
- move the camera (totals after the move, within the owner's limits): {"kind": "camera", "turn": 0, "height": 0, "back": 0, "fov": 0}
- take a piece out of the shot: {"kind": "remove", "id": "<an id from the list>"}
- ceiling finish: {"kind": "ceiling", "finish": "plaster"} or "concrete"
- exposure (the total offset, -1 to +1 stops): {"kind": "exposure", "stops": 0.5}
- the room's own lamps: {"kind": "lamps", "state": "on"} or "off"
- {"kind": "none"}

Answer with exactly one JSON object and nothing else:
{"problem": "<one sentence>", "fix": {...}, "why": "<one sentence: what the fix changes in the picture>"}
```

# The comparer (fixed prompt; 3 per step, the order of A and B drawn at random)

```
Two versions of the same listing photo for an estate agency. Open both with the Read tool and look at them carefully:
- {dir}/A.jpg
- {dir}/B.jpg

Do not open any other file and do not run commands. Which one would the agency rather publish? Answer with exactly:
choice: A or B
why: <one sentence>
```
