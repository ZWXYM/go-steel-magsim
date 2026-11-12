# Frequency holdout example: HEFMAG laser-scribed GO

`original_20_measurement_references.csv` is a transcription of the 20 reference loss values and their standard uncertainties on page 20 of Michal Ulvr's HEFMAG extended Epstein comparison report ([Zenodo DOI 10.5281/zenodo.10032756](https://doi.org/10.5281/zenodo.10032756), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)). The CSV adds explicit units and the reported strip configuration for each frequency. The alternate statistics on page 22 are kept out of this example.

Upload the CSV to “多频损耗复核”, select “公开测量参考统计”, add the DOI as the source, and hold out 100, 200 or 400 Hz. The 12 target values were checked with a separate constrained least-squares calculation. The comparisons in `benchmark.json` include direct frequency scaling from 50 Hz and interpolation of loss per cycle between neighboring training frequencies. The holdout fits overlap in their training data.

The study uses the source's peak magnetic polarization J, loss in W/kg and strip configurations that vary by frequency. It is a within-table comparison. The sample is not identified as any of the four pilot grades, and the fitted values are not motor loss coefficients.
