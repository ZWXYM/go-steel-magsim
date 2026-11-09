# Frozen study configuration to original optimizer

This software increment applies one explicitly chosen existing constraint-study configuration to the unchanged original scorer. It creates a separate original optimization record and retains the study, source dataset, selected conditions and four original output files. Repeating the same source/configuration reopens the saved record. No motor solve, training or prediction is started.

The same six archived V8_2 library-material cases are used for both rows. c33 is the original default (ripple reduction >=30%, torque change >=0%, iron loss increase <=0%) and still has zero candidates. The explicitly chosen c30 allows torque change >=-0.5% and gives one candidate, nippon_steel_23zdkh75. This does not alter the default or establish a new final physical optimum.

The saved candidate's original relative metrics are torque -0.158846%, ripple reduction 42.207065%, iron-loss reduction 2.398146%. Historical loss and same-version assumptions follow the archived dataset; these are not new four-sample calibrated-material motor results. Twelve comparison rows are two configuration views of six existing cases, not twelve independent experiments.

The UI now links the saved result back to the original optimization page. Actual HTTP create/reopen/download, default-zero preservation, cached repeat, original score/source equality and software checks are recorded in validation.json. No browser surface was enabled for this run; browser visual acceptance is not claimed. Full private application ZIPs are retained locally and not published.
