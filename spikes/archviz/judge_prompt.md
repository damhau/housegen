# The judge's prompt (fixed for every round)

Each judge is a fresh general-purpose subagent with no other context. `{dir}` is `work/judge/<round>/j<k>` (absolute
path). `judge_save.py <round>/j<k>=<the subagent's transcript>` saves each reply as `answer.md`, and
`judge_tab.py <round dir>` unblinds them with `key.json` and prints the medians.

```
You are judging two interior images for an estate agency's sales brochure. Open both files with the Read tool and look at them carefully:
- {dir}/A.jpg
- {dir}/B.jpg

Do not open any other file and do not run commands. Score EACH image from 1 to 10 on each line below. Be strict and calibrated: 10 = flawless, as good as the best architectural-visualisation studios; 8 = clearly professional, an agency would publish it as is; 6 = decent but visibly lacking; 4 = amateur; 2 = very poor.

1. Daylight and bounce light (direct sun, soft indirect light, shadows, contact shadows)
2. Materials (floor, walls, ceiling, fabrics: texture, detail, realism)
3. Soft furnishings and decor (cushions, throws, rugs, plants, flowers, accessories, styling)
4. The view through the windows (what is outside, how believable)
5. Colour grade (white balance, contrast, overall tone)
6. Overall: would an estate agency publish it?

Answer with exactly this format and nothing else:

| line | A | B |
|---|---|---|
| Daylight | x | x |
| Materials | x | x |
| Decor | x | x |
| View | x | x |
| Grade | x | x |
| Overall | x | x |

A weakest point: <one sentence>
B weakest point: <one sentence>
```
