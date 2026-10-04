# Sampling diagnostic figure contract

Core claim: fixed-distribution, nested-prefix comparisons reveal finite-grain variability that one H800 metric cannot fully describe; N64 is a comparison, not a certified limit.

Archetype: quantitative grid. Hero panels a/b show complete guarded ensemble curves versus the same physical H axis for RD/TD; subordinate panels c/d show all recorded nested grain counts and both predeclared seeds at H800. No reference-fit curve is included. The figure makes no experimental material, independent calibration or loss claim.

Backend: project Python/Matplotlib workflow, saved Python preference. Export contract: 183 mm wide by 125 mm high; editable text SVG/PDF, PNG preview at 300 dpi and TIFF at 600 dpi. Axes/legends use 7 pt sans-serif text, panel letters 8 pt. All quantitative source data are written as CSV and source hashes are recorded.

Data: four complete N64 ensembles, B30P105 RD/TD and two manifest seeds. Prefixes N4/N8/N16/N32/N64 reuse each completed realization, not new draws. RD and TD use the same orientation rows within a seed. Mixture parameters are report priors and finite realized fractions are stored separately. All positive-H nodes are plotted on log H; H=0 is excluded from the log display only and retained in source data. No grain or seed is removed.

Statistics: arithmetic equal-volume mean of per-grain guarded major-loop midpoint proxies. Grain counts are simulation units, not biological or experimental replicates. Two seeds are independent numerical realizations; no experimental confidence intervals, p values, material split or calibration fitting appear in this figure. Prefix lines illustrate sensitivity and do not imply monotone convergence.

Reviewer risks: largest N is finite; Goss scatter is a clipped half-normal angle prior rather than independently measured ODF HWHM; seed-to-seed finite mixture fractions vary; guard can alter the midpoint proxy and its magnitude is recorded; full-curve sampling differences can exceed H800 differences. Readability/export QA must be checked after all four ensembles finish.
