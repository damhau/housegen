# SIA 416 report mockup (#47, #48)

The mockup is the spec for the SIA 416 report: the implementation reproduces it page for page.
Published at https://claude.ai/artifact/1edugSFakZDZL44pFUmZC5 (private; `mockup.html` is the same page).

The figures were computed from TestVillaGille version 10 on dev: its room layout (`interior-layouts.js`,
`dimensions.js`, pure data) plus values transcribed by hand from its other files (openings, roof
windows, tiled walls, garden, pools). The implementation must read all of this from the tagged scene
instead; the list is in the spec comment on #47.

```bash
# the scene's sources, from dev (no auth)
mkdir -p /tmp/villa && cd /tmp/villa
curl -s https://housegen-dev.apps.dhconsulting.ch/api/v1/projects/fa8bd00053e6/files -o files.json
python3 -c "import json,os; [ (os.makedirs(os.path.dirname(f['path']),exist_ok=True), open(f['path'],'w').write(f['content'])) for f in json.load(open('files.json')) ]"
cd -  # back to spikes/sia416
node sia416.mjs /tmp/villa/src sia416.json              # areas, walls, openings, volumes
node rlatc.mjs sia416.json extra.json                    # RLATC art. 25/27/28, exterior works
node build-report.mjs sia416.json mockup.html extra.json # the page (no <html>/<head>: the artifact host adds them)
```

Reference figures (the implementation must reproduce them on TestVillaGille v10):
SP 440.00 m², SN 354.51 m², SC 85.49 m², SUP 224.95 / SUS 67.04 / SD 49.68 / SI 12.83 m²,
VB 1'270.34 m³ (sous-sol 335.50), garage 101.64 m³, 32 take-off lines,
6 rooms more than 5 % off the plan (0.03, 0.07, 1.04, 2.01, 2.03, 2.04),
surface habitable App. 1 113.55 m² (pondérée 119.17), App. 2 130.10 m² (142.64).
