# The listing-set judge (the wall-art library, 2026-11-02)

One house's photos judged together, the way a buyer browses a listing: two sets of the same rooms from the same
cameras, A and B in a random order per judge. Each judge is a fresh general-purpose subagent with no other context.
`listing.py pair <round> <arm A tag> <arm B tag> <rooms...>` writes `work/judge/<round>/j<k>/{A,B}<n>.jpg` and the
key; `listing.py tab <round> <answers...>` unblinds.

```
You are judging two sets of interior photos for an estate agency's listing of one house. Each set shows the same six rooms. Open all twelve files with the Read tool and look at them carefully, one set after the other:
- {dir}/A1.jpg ... {dir}/A6.jpg
- {dir}/B1.jpg ... {dir}/B6.jpg

Do not open any other file and do not run commands. Judge each SET as a whole, as a buyer scrolling through the listing would see it. Score each set from 1 to 10 on each line (10 = flawless, 8 = an agency would publish it as is, 6 = decent but visibly lacking, 4 = amateur):

1. Decor and styling across the set (art, textiles, plants, accessories: do they suit each room?)
2. Variety: does the set feel like one real, lived-in home, or do the same objects and decor repeat from photo to photo?
3. Overall: which listing would an agency rather publish?

Answer with exactly this format and nothing else:

| line | A | B |
|---|---|---|
| Decor | x | x |
| Variety | x | x |
| Overall | x | x |

Repeats in A: <the objects or pictures you saw in more than one photo of set A, or "none">
Repeats in B: <the same for set B>
Wall art: <one sentence on the pictures on the walls of each set>
```
