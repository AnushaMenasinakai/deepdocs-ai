# Phase16A-R — real cross-encoder evaluation

B. DO NOT INTEGRATE

Model `cross-encoder/ms-marco-MiniLM-L6-v2` (approved spelling `cross-encoder/ms-marco-MiniLM-L-6-v2`), revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`. Raw scalar logits, identity activation; relevance scores are NOT calibrated probabilities or factual confidence.

## Measured setup

{
  "measurement": "Real local CrossEncoder raw logits; identity activation; no sigmoid",
  "model": "cross-encoder/ms-marco-MiniLM-L6-v2",
  "requested_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
  "revision": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
  "source": "https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2",
  "device": "cpu",
  "batch_size": 16,
  "threads": 4,
  "max_length": 512,
  "maximum_pair_tokens": 65,
  "truncated_pairs": 0,
  "pairs_scored": 225,
  "benchmark_pairs": 175,
  "safety_pairs": 50,
  "runtime": {
    "sentence-transformers": "6.1.0",
    "torch": "2.14.0",
    "transformers": "5.17.0"
  },
  "snapshot_file_bytes": {
    "config.json": 794,
    "model.safetensors": 90870598,
    "tokenizer.json": 711396,
    "tokenizer_config.json": 1330,
    "special_tokens_map.json": 132,
    "vocab.txt": 231508,
    "README.md": 3673
  },
  "snapshot_sha256": {
    "config.json": "380e02c93f431831be65d99a4e7e5f67c133985bf2e77d9d4eba46847190bacc",
    "model.safetensors": "821d1aa69520101d6e0737f78a042ae25b19e5cb9160701909d10434f4aeb0ae",
    "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    "tokenizer_config.json": "a5c2e5a7b1a29a0702cd28c08a399b5ecc110c263009d17f7e3b415f25905fd8",
    "special_tokens_map.json": "3c3507f36dff57bce437223db3b3081d1e2b52ec3e56ee55438193ecb2c94dd6",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
    "README.md": "7c0ec39941d0d1d766ccde671f592971d82fe45b7eea550a80d1ea864ebc3baa"
  },
  "candidate_fingerprint": "57dc8084c267e2941578aedd8bebffcb7e8cb5c178f4fbbe07d7919da9d2effd",
  "historical_hashes": {
    "fixture.json": "d8c5295ca7d07ace96e37cfdfa7dc54dafb76aad8389c6977abe4b10db9ef51d",
    "fixture.md": "72af67adf6eeca0ca69e2951704b3180a184f20c3e96a0c5843bcd55fc172147",
    "local-model.json": "d934000d1d912c10219cf2ccfb5b882269086fcb2c010ca17b6923a45ea3e501",
    "local-model.md": "f36e1b5dc652ef4a388513df14ffe70d4910423e501129061691e31ff972efc2",
    "phase13-comparison.json": "8879ee134930b4106df9ffec23a51ebd691d29353e7a19a7805f1169ccf6e989",
    "phase13-comparison.md": "6e9e143875c92d8ce7eddb15fc0c3c99ede11bfb733b2f36ea0ba98f96d2141d",
    "phase15a-multi-query.json": "f08b4b8ce8d957e0081df88b9499c3a6a13140dba319f18a33d69d03ed49254f",
    "phase15a-multi-query.md": "b309dd5e46453e2678df4bdb36c3877c20bf52f6722741ffd3c8bf5a6d6e8aa5",
    "phase16a-reranking.json": "890be3331a4e541bad945ebe5e6ec58bf90516181afbeaad745bbfde080e1a12",
    "phase16a-reranking.md": "d632bcafd8b1d5a57898611842f30efa495cb5de0b61223dc2c4df233d1ec9e0"
  }
}

## Ranking, distributions, and cost

Baseline: `{'answerable_count': 22, 'unsupported_count': 13, 'no_valid_hit_count': 0, 'hit_at_1': 0.9545454545454546, 'hit_at_3': 1.0, 'hit_at_5': 1.0, 'mrr': 0.9772727272727273, 'all_evidence_at_5': 1.0}`

Reranked: `{'answerable_count': 22, 'unsupported_count': 13, 'no_valid_hit_count': 0, 'hit_at_1': 0.9545454545454546, 'hit_at_3': 1.0, 'hit_at_5': 1.0, 'mrr': 0.9696969696969696, 'all_evidence_at_5': 1.0}`

Movements: `{'required_promoted': 3, 'required_demoted': 1, 'non_required_promoted': 43, 'non_required_demoted': 50}`

{
  "required_answerable": {
    "count": 27,
    "minimum": -11.003318786621094,
    "maximum": 10.340118408203125,
    "mean": 1.1745347055020157,
    "median": 3.6971840858459473,
    "p10": -8.507556915283203,
    "p90": 8.950789070129396
  },
  "non_required_answerable": {
    "count": 83,
    "minimum": -11.492352485656738,
    "maximum": 6.410076141357422,
    "mean": -7.982397583593805,
    "median": -9.539860725402832,
    "p10": -11.424337005615234,
    "p90": -3.251195669174195
  },
  "unsupported": {
    "count": 65,
    "minimum": -11.438095092773438,
    "maximum": 1.7596919536590576,
    "mean": -8.701096391677856,
    "median": -11.19815444946289,
    "p10": -11.409274291992187,
    "p90": -3.655569744110107
  }
}

{
  "model_load_seconds": 47.58261199994013,
  "inference_seconds": 1.5913556000450626,
  "average_seconds_per_question": 0.03536345777877917,
  "candidate_pairs_per_second": 141.3888888150635,
  "note": "CPU four threads, 225 pairs batched across 45 questions; cold predict call, excludes model load and token-length audit. Not production request latency."
}

## Policies and selection

A = production cosine .50 gate / .30 evidence. B = reranker gate, keeping all passages above the evidence threshold. Single-threshold and offsets 2/4 logit units are evaluated. No score-space arithmetic.

Not evaluated: a 0.50 cosine intersection cannot recover the eight failures; no independent evidence justifies choosing a lower sanity floor. Adding one would introduce another tuned parameter.

Development criteria pass first, then satisfied criterion count, fewer FP, completeness, multi completeness, recall, fewer added non-required passages, stricter thresholds; safety never used to choose.

Frozen development comparator: primary **3.0**, evidence **-1.0**. Production candidate selected: **False**.

Decision {'tp': 15, 'tn': 13, 'fp': 0, 'fn': 7, 'precision': 1.0, 'recall': 0.6818181818181818, 'f1': 0.8108108108108109}; complete 13/22; multi 1/5.

Recovered FNs: ['direct-password', 'ambiguous-data']; corrected FPs: ['unsupported-refresh', 'unsupported-rotation']; new FPs: [].

Criteria: `{'recover_at_least_four': False, 'correct_at_least_one': True, 'at_most_one_new_fp': True, 'complete_at_least_eighteen': False, 'multi_at_least_four': False, 'precision_at_least_0875': True, 'recall_improved': True, 'context_bounded': True}`. Final checks: `{'development_criteria_pass': False, 'safety_no_new_fp_and_precision_preserved': True, 'local_compute_under_two_seconds_per_question': True}`.

## Full bounded development sweep

| Gate | Evidence | TP/TN/FP/FN | P/R/F1 | Complete /22 | Multi /5 | Added non-required / removed required | Avg/max chunks | Avg/max chars | Budget cases |
|---|---|---|---|---|---|---|---|---|---|
| -12.0 | -12.0 | 22/0/13/0 | 0.62857/1.00000/0.77193 | 22 | 5 | 103/0 | 5.000/5 | 1634.00/1725 | 0 |
| -11.0 | -11.0 | 22/6/7/0 | 0.75862/1.00000/0.86275 | 21 | 4 | 49/0 | 4.034/5 | 1322.83/1725 | 0 |
| -10.0 | -10.0 | 22/6/7/0 | 0.75862/1.00000/0.86275 | 21 | 4 | 32/0 | 3.276/5 | 1073.07/1725 | 0 |
| -9.0 | -9.0 | 22/7/6/0 | 0.78571/1.00000/0.88000 | 19 | 3 | 25/1 | 3.036/5 | 993.18/1725 | 0 |
| -8.0 | -8.0 | 22/7/6/0 | 0.78571/1.00000/0.88000 | 19 | 3 | 22/1 | 2.786/5 | 911.14/1725 | 0 |
| -7.0 | -7.0 | 20/7/6/2 | 0.76923/0.90909/0.83333 | 18 | 3 | 17/1 | 2.615/5 | 855.81/1725 | 0 |
| -6.0 | -6.0 | 19/8/5/3 | 0.79167/0.86364/0.82609 | 15 | 1 | 10/3 | 2.250/5 | 731.33/1581 | 0 |
| -5.0 | -5.0 | 19/8/5/3 | 0.79167/0.86364/0.82609 | 15 | 1 | 9/3 | 2.125/5 | 689.50/1581 | 0 |
| -4.0 | -4.0 | 18/9/4/4 | 0.81818/0.81818/0.81818 | 15 | 1 | 6/3 | 1.864/4 | 605.27/1296 | 0 |
| -3.0 | -3.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 4/3 | 1.600/3 | 519.20/965 | 0 |
| -2.0 | -2.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 4/3 | 1.550/3 | 503.45/965 | 0 |
| -1.0 | -1.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 3/3 | 1.350/3 | 439.55/965 | 0 |
| 0.0 | 0.0 | 15/10/3/7 | 0.83333/0.68182/0.75000 | 12 | 0 | 2/5 | 1.278/3 | 414.44/965 | 0 |
| 1.0 | 1.0 | 15/11/2/7 | 0.88235/0.68182/0.76923 | 12 | 0 | 2/5 | 1.294/3 | 419.94/965 | 0 |
| 2.0 | 2.0 | 15/13/0/7 | 1.00000/0.68182/0.81081 | 12 | 0 | 0/5 | 1.200/2 | 388.33/636 | 0 |
| 3.0 | 3.0 | 15/13/0/7 | 1.00000/0.68182/0.81081 | 12 | 0 | 0/5 | 1.200/2 | 388.33/636 | 0 |
| 4.0 | 4.0 | 13/13/0/9 | 1.00000/0.59091/0.74286 | 12 | 0 | 0/7 | 1.154/2 | 374.46/636 | 0 |
| 5.0 | 5.0 | 11/13/0/11 | 1.00000/0.50000/0.66667 | 11 | 0 | 0/8 | 1.182/2 | 384.18/636 | 0 |
| 6.0 | 6.0 | 7/13/0/15 | 1.00000/0.31818/0.48276 | 7 | 0 | 0/11 | 1.143/2 | 362.86/636 | 0 |
| 7.0 | 7.0 | 7/13/0/15 | 1.00000/0.31818/0.48276 | 7 | 0 | 0/11 | 1.000/1 | 317.86/330 | 0 |
| 8.0 | 8.0 | 6/13/0/16 | 1.00000/0.27273/0.42857 | 6 | 0 | 0/12 | 1.000/1 | 317.33/330 | 0 |
| 9.0 | 9.0 | 3/13/0/19 | 1.00000/0.13636/0.24000 | 3 | 0 | 0/15 | 1.000/1 | 312.33/317 | 0 |
| 10.0 | 10.0 | 1/13/0/21 | 1.00000/0.04545/0.08696 | 1 | 0 | 0/17 | 1.000/1 | 304.00/304 | 0 |
| 11.0 | 11.0 | 0/13/0/22 | 0.00000/0.00000/0.00000 | 0 | 0 | 0/18 | 0.000/0 | 0.00/2 | 0 |
| -12.0 | -14.0 | 22/0/13/0 | 0.62857/1.00000/0.77193 | 22 | 5 | 103/0 | 5.000/5 | 1634.00/1725 | 0 |
| -11.0 | -13.0 | 22/6/7/0 | 0.75862/1.00000/0.86275 | 22 | 5 | 73/0 | 5.000/5 | 1634.90/1725 | 0 |
| -10.0 | -12.0 | 22/6/7/0 | 0.75862/1.00000/0.86275 | 22 | 5 | 73/0 | 5.000/5 | 1634.90/1725 | 0 |
| -9.0 | -11.0 | 22/7/6/0 | 0.78571/1.00000/0.88000 | 21 | 4 | 45/0 | 4.036/5 | 1323.14/1725 | 0 |
| -8.0 | -10.0 | 22/7/6/0 | 0.78571/1.00000/0.88000 | 21 | 4 | 31/0 | 3.357/5 | 1100.54/1725 | 0 |
| -7.0 | -9.0 | 20/7/6/2 | 0.76923/0.90909/0.83333 | 18 | 3 | 24/1 | 3.192/5 | 1043.54/1725 | 0 |
| -6.0 | -8.0 | 19/8/5/3 | 0.79167/0.86364/0.82609 | 17 | 3 | 18/1 | 3.000/5 | 977.04/1725 | 0 |
| -5.0 | -7.0 | 19/8/5/3 | 0.79167/0.86364/0.82609 | 17 | 3 | 14/1 | 2.667/5 | 869.38/1725 | 0 |
| -4.0 | -6.0 | 18/9/4/4 | 0.81818/0.81818/0.81818 | 15 | 1 | 8/3 | 2.318/5 | 752.77/1581 | 0 |
| -3.0 | -5.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 6/3 | 2.300/5 | 743.90/1581 | 0 |
| -2.0 | -4.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 5/3 | 1.950/4 | 631.00/1296 | 0 |
| -1.0 | -3.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 4/3 | 1.600/3 | 519.20/965 | 0 |
| 0.0 | -2.0 | 15/10/3/7 | 0.83333/0.68182/0.75000 | 13 | 1 | 4/4 | 1.556/3 | 502.67/965 | 0 |
| 1.0 | -1.0 | 15/11/2/7 | 0.88235/0.68182/0.76923 | 13 | 1 | 3/4 | 1.412/3 | 458.59/965 | 0 |
| 2.0 | 0.0 | 15/13/0/7 | 1.00000/0.68182/0.81081 | 12 | 0 | 0/5 | 1.267/3 | 410.27/965 | 0 |
| 3.0 | 1.0 | 15/13/0/7 | 1.00000/0.68182/0.81081 | 12 | 0 | 0/5 | 1.267/3 | 410.27/965 | 0 |
| 4.0 | 2.0 | 13/13/0/9 | 1.00000/0.59091/0.74286 | 12 | 0 | 0/7 | 1.231/2 | 398.69/636 | 0 |
| 5.0 | 3.0 | 11/13/0/11 | 1.00000/0.50000/0.66667 | 11 | 0 | 0/8 | 1.273/2 | 412.82/636 | 0 |
| 6.0 | 4.0 | 7/13/0/15 | 1.00000/0.31818/0.48276 | 7 | 0 | 0/11 | 1.286/2 | 408.57/636 | 0 |
| 7.0 | 5.0 | 7/13/0/15 | 1.00000/0.31818/0.48276 | 7 | 0 | 0/11 | 1.286/2 | 408.57/636 | 0 |
| 8.0 | 6.0 | 6/13/0/16 | 1.00000/0.27273/0.42857 | 6 | 0 | 0/12 | 1.000/1 | 317.33/330 | 0 |
| 9.0 | 7.0 | 3/13/0/19 | 1.00000/0.13636/0.24000 | 3 | 0 | 0/15 | 1.000/1 | 312.33/317 | 0 |
| 10.0 | 8.0 | 1/13/0/21 | 1.00000/0.04545/0.08696 | 1 | 0 | 0/17 | 1.000/1 | 304.00/304 | 0 |
| 11.0 | 9.0 | 0/13/0/22 | 0.00000/0.00000/0.00000 | 0 | 0 | 0/18 | 0.000/0 | 0.00/2 | 0 |
| -12.0 | -16.0 | 22/0/13/0 | 0.62857/1.00000/0.77193 | 22 | 5 | 103/0 | 5.000/5 | 1634.00/1725 | 0 |
| -11.0 | -15.0 | 22/6/7/0 | 0.75862/1.00000/0.86275 | 22 | 5 | 73/0 | 5.000/5 | 1634.90/1725 | 0 |
| -10.0 | -14.0 | 22/6/7/0 | 0.75862/1.00000/0.86275 | 22 | 5 | 73/0 | 5.000/5 | 1634.90/1725 | 0 |
| -9.0 | -13.0 | 22/7/6/0 | 0.78571/1.00000/0.88000 | 22 | 5 | 68/0 | 5.000/5 | 1634.57/1725 | 0 |
| -8.0 | -12.0 | 22/7/6/0 | 0.78571/1.00000/0.88000 | 22 | 5 | 68/0 | 5.000/5 | 1634.57/1725 | 0 |
| -7.0 | -11.0 | 20/7/6/2 | 0.76923/0.90909/0.83333 | 19 | 4 | 39/0 | 4.038/5 | 1322.88/1725 | 0 |
| -6.0 | -10.0 | 19/8/5/3 | 0.79167/0.86364/0.82609 | 18 | 4 | 24/0 | 3.500/5 | 1144.50/1725 | 0 |
| -5.0 | -9.0 | 19/8/5/3 | 0.79167/0.86364/0.82609 | 17 | 3 | 20/1 | 3.250/5 | 1059.42/1725 | 0 |
| -4.0 | -8.0 | 18/9/4/4 | 0.81818/0.81818/0.81818 | 17 | 3 | 14/1 | 3.045/5 | 991.05/1725 | 0 |
| -3.0 | -7.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 16 | 3 | 8/1 | 2.800/5 | 910.15/1725 | 0 |
| -2.0 | -6.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 6/3 | 2.400/5 | 777.25/1581 | 0 |
| -1.0 | -5.0 | 17/10/3/5 | 0.85000/0.77273/0.80952 | 14 | 1 | 6/3 | 2.300/5 | 743.90/1581 | 0 |
| 0.0 | -4.0 | 15/10/3/7 | 0.83333/0.68182/0.75000 | 13 | 1 | 5/4 | 2.000/4 | 644.39/1296 | 0 |
| 1.0 | -3.0 | 15/11/2/7 | 0.88235/0.68182/0.76923 | 13 | 1 | 4/4 | 1.588/3 | 513.35/965 | 0 |
| 2.0 | -2.0 | 15/13/0/7 | 1.00000/0.68182/0.81081 | 13 | 1 | 1/4 | 1.533/3 | 495.27/965 | 0 |
| 3.0 | -1.0 | 15/13/0/7 | 1.00000/0.68182/0.81081 | 13 | 1 | 1/4 | 1.400/3 | 454.07/965 | 0 |
| 4.0 | 0.0 | 13/13/0/9 | 1.00000/0.59091/0.74286 | 12 | 0 | 0/7 | 1.308/3 | 424.00/965 | 0 |
| 5.0 | 1.0 | 11/13/0/11 | 1.00000/0.50000/0.66667 | 11 | 0 | 0/8 | 1.364/3 | 442.73/965 | 0 |
| 6.0 | 2.0 | 7/13/0/15 | 1.00000/0.31818/0.48276 | 7 | 0 | 0/11 | 1.429/2 | 453.57/636 | 0 |
| 7.0 | 3.0 | 7/13/0/15 | 1.00000/0.31818/0.48276 | 7 | 0 | 0/11 | 1.429/2 | 453.57/636 | 0 |
| 8.0 | 4.0 | 6/13/0/16 | 1.00000/0.27273/0.42857 | 6 | 0 | 0/12 | 1.167/2 | 370.67/636 | 0 |
| 9.0 | 5.0 | 3/13/0/19 | 1.00000/0.13636/0.24000 | 3 | 0 | 0/15 | 1.333/2 | 419.00/636 | 0 |
| 10.0 | 6.0 | 1/13/0/21 | 1.00000/0.04545/0.08696 | 1 | 0 | 0/17 | 1.000/1 | 304.00/304 | 0 |
| 11.0 | 7.0 | 0/13/0/22 | 0.00000/0.00000/0.00000 | 0 | 0 | 0/18 | 0.000/0 | 0.00/2 | 0 |

## Every case and candidate (trusted original top five only)

### direct-signature

How does the server detect a modified JWT?

Expected evidence: [{'document': 'authentication-guide.pdf', 'page': 1}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p1–1 | 1 / 0.62515331 | 1 / 5.32876062 | True | True | False | True |
| web-guide.pdf p1–1 | 2 / 0.42049938 | 2 / -1.88712907 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.36542620 | 3 / -3.42244601 | False | False | False | False |
| operations-guide.pdf p2–2 | 3 / 0.39812872 | 4 / -3.83537197 | False | False | False | False |
| authentication-guide.pdf p2–2 | 5 / 0.30509447 | 5 / -11.33257771 | False | False | False | False |
### direct-expiry

How long does an access token remain valid?

Expected evidence: [{'document': 'authentication-guide.pdf', 'page': 2}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p2–2 | 1 / 0.70177221 | 1 / 8.11103821 | True | True | False | True |
| operations-guide.pdf p3–3 | 4 / 0.44290268 | 2 / 3.14165449 | False | True | False | True |
| operations-guide.pdf p2–2 | 3 / 0.47920420 | 3 / 1.77681911 | False | True | False | False |
| web-guide.pdf p1–1 | 5 / 0.34699727 | 4 / -4.17494774 | False | False | False | False |
| web-guide.pdf p2–2 | 2 / 0.49766336 | 5 / -4.31780720 | False | False | False | False |
### direct-password

Which algorithm stores password hashes?

Expected evidence: [{'document': 'authentication-guide.pdf', 'page': 3}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p3–3 | 1 / 0.44279455 | 1 / 4.14060402 | True | True | True | True |
| data-guide.pdf p2–2 | 3 / 0.25276707 | 2 / -8.84477615 | False | False | False | False |
| data-guide.pdf p1–1 | 2 / 0.28860458 | 3 / -10.24032688 | False | False | False | False |
| storage-guide.pdf p1–1 | 5 / 0.16049525 | 4 / -10.93934250 | False | False | False | False |
| operations-guide.pdf p1–1 | 4 / 0.17411221 | 5 / -11.41979218 | False | False | False | False |
### direct-upload

What is the maximum PDF upload size?

Expected evidence: [{'document': 'storage-guide.pdf', 'page': 1}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| storage-guide.pdf p1–1 | 1 / 0.54185322 | 1 / 5.41508770 | True | True | False | True |
| web-guide.pdf p3–3 | 2 / 0.52924683 | 2 / -4.65570879 | False | False | False | False |
| storage-guide.pdf p2–2 | 3 / 0.33722651 | 3 / -8.57543182 | False | False | False | False |
| data-guide.pdf p2–2 | 5 / 0.19011965 | 4 / -9.17345428 | False | False | False | False |
| storage-guide.pdf p3–3 | 4 / 0.23141009 | 5 / -10.67513943 | False | False | False | False |
### direct-backup

How often does North back up metadata?

Expected evidence: [{'document': 'operations-guide.pdf', 'page': 1}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p1–1 | 1 / 0.65782418 | 1 / 7.05725384 | True | True | False | True |
| operations-guide.pdf p4–4 | 2 / 0.65649495 | 2 / 6.41007614 | False | True | False | True |
| storage-guide.pdf p1–1 | 5 / 0.21694903 | 3 / -10.75125027 | False | False | False | False |
| data-guide.pdf p1–1 | 3 / 0.29776715 | 4 / -10.95866776 | False | False | False | False |
| web-guide.pdf p3–3 | 4 / 0.26586694 | 5 / -11.09136391 | False | False | False | False |
### para-signature

What stops someone editing a login token and passing it off as genuine?

Expected evidence: [{'document': 'authentication-guide.pdf', 'page': 1}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p2–2 | 3 / 0.39186361 | 1 / -7.40866709 | False | False | False | False |
| web-guide.pdf p1–1 | 2 / 0.40223454 | 2 / -9.33055687 | False | False | False | False |
| authentication-guide.pdf p1–1 | 1 / 0.41665697 | 3 / -9.81566238 | True | False | False | False |
| authentication-guide.pdf p3–3 | 5 / 0.30985787 | 4 / -10.78360367 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.34184004 | 5 / -10.98768234 | False | False | False | False |
### para-paas

Which cloud offering lets developers deploy code without caring for the operating system?

Expected evidence: [{'document': 'cloud-guide.pdf', 'page': 2}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| cloud-guide.pdf p2–2 | 1 / 0.59114677 | 1 / 5.29958773 | True | True | False | True |
| cloud-guide.pdf p1–1 | 3 / 0.30608018 | 2 / -9.60012627 | False | False | False | False |
| cloud-guide.pdf p3–3 | 2 / 0.37356678 | 3 / -10.55127335 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.14707056 | 4 / -11.25576210 | False | False | False | False |
| data-guide.pdf p2–2 | 5 / 0.10037136 | 5 / -11.37334251 | False | False | False | False |
### para-owner

Can a different account discover my Knowledge Base by guessing its identifier?

Expected evidence: [{'document': 'authentication-guide.pdf', 'page': 4}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p4–4 | 1 / 0.34289047 | 1 / -0.06486091 | True | False | False | False |
| data-guide.pdf p2–2 | 5 / 0.17418029 | 2 / -8.44307327 | False | False | False | False |
| data-guide.pdf p1–1 | 3 / 0.19856192 | 3 / -9.22201920 | False | False | False | False |
| authentication-guide.pdf p3–3 | 2 / 0.27867466 | 4 / -11.37389278 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.17556605 | 5 / -11.44865608 | False | False | False | False |
### para-ocr

Can the system read words from a scan that contains pictures but no selectable text?

Expected evidence: [{'document': 'storage-guide.pdf', 'page': 2}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| storage-guide.pdf p2–2 | 1 / 0.48013180 | 1 / -7.88607788 | True | False | False | False |
| data-guide.pdf p2–2 | 2 / 0.23136444 | 2 / -10.00804710 | False | False | False | False |
| data-guide.pdf p1–1 | 4 / 0.15502933 | 3 / -10.10427094 | False | False | False | False |
| storage-guide.pdf p3–3 | 3 / 0.19077710 | 4 / -11.06068802 | False | False | False | False |
| web-guide.pdf p3–3 | 5 / 0.13416093 | 5 / -11.38025856 | False | False | False | False |
### para-history

Will an earlier response be remembered automatically when I ask something new?

Expected evidence: [{'document': 'data-guide.pdf', 'page': 3}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| data-guide.pdf p3–3 | 2 / 0.34304364 | 1 / -6.94405603 | True | False | False | False |
| web-guide.pdf p2–2 | 1 / 0.36147529 | 2 / -9.77134514 | False | False | False | False |
| authentication-guide.pdf p2–2 | 4 / 0.14194255 | 3 / -11.35415459 | False | False | False | False |
| operations-guide.pdf p2–2 | 5 / 0.11561224 | 4 / -11.45858383 | False | False | False | False |
| data-guide.pdf p1–1 | 3 / 0.14792566 | 5 / -11.47848892 | False | False | False | False |
### multi-storage

Where are PDF originals kept, and where are their embedding vectors kept?

Expected evidence: [{'document': 'storage-guide.pdf', 'page': 1}, {'document': 'data-guide.pdf', 'page': 2}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| storage-guide.pdf p1–1 | 1 / 0.52978851 | 1 / -0.79987085 | True | False | False | False |
| data-guide.pdf p1–1 | 2 / 0.42890173 | 2 / -1.86813188 | False | False | False | False |
| storage-guide.pdf p3–3 | 4 / 0.38950052 | 3 / -5.71918774 | False | False | False | False |
| data-guide.pdf p2–2 | 5 / 0.32490101 | 4 / -6.41016531 | True | False | False | False |
| storage-guide.pdf p2–2 | 3 / 0.41915743 | 5 / -7.31878901 | False | False | False | False |
### multi-cloud

Compare what the customer manages in IaaS and PaaS.

Expected evidence: [{'document': 'cloud-guide.pdf', 'page': 1}, {'document': 'cloud-guide.pdf', 'page': 2}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| cloud-guide.pdf p1–1 | 1 / 0.27171542 | 1 / -4.76045513 | True | False | False | False |
| cloud-guide.pdf p3–3 | 2 / 0.26971811 | 2 / -9.70931244 | False | False | False | False |
| cloud-guide.pdf p2–2 | 5 / 0.11700260 | 3 / -11.00331879 | True | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.11960169 | 4 / -11.42547321 | False | False | False | False |
| authentication-guide.pdf p3–3 | 3 / 0.15951189 | 5 / -11.49171829 | False | False | False | False |
### multi-token

How are token tampering and expired access handled?

Expected evidence: [{'document': 'authentication-guide.pdf', 'page': 1}, {'document': 'authentication-guide.pdf', 'page': 2}]. Accepted: True; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p2–2 | 1 / 0.58086475 | 1 / 3.01160431 | True | True | False | True |
| operations-guide.pdf p3–3 | 2 / 0.44285604 | 2 / -3.20838308 | False | False | False | False |
| web-guide.pdf p2–2 | 3 / 0.42214915 | 3 / -3.97922683 | False | False | False | False |
| web-guide.pdf p1–1 | 4 / 0.38093384 | 4 / -7.39076710 | False | False | False | False |
| authentication-guide.pdf p1–1 | 5 / 0.36325441 | 5 / -9.43977547 | True | False | False | False |
### multi-deletion

What happens to document vectors and old answers when a PDF is deleted?

Expected evidence: [{'document': 'storage-guide.pdf', 'page': 3}, {'document': 'data-guide.pdf', 'page': 3}]. Accepted: True; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| storage-guide.pdf p3–3 | 1 / 0.62864603 | 1 / 3.69718409 | True | True | False | True |
| data-guide.pdf p1–1 | 5 / 0.33204861 | 2 / -5.57051945 | False | False | False | False |
| storage-guide.pdf p2–2 | 2 / 0.43748395 | 3 / -6.19551039 | False | False | False | False |
| data-guide.pdf p3–3 | 4 / 0.36453979 | 4 / -6.33914089 | True | False | False | False |
| storage-guide.pdf p1–1 | 3 / 0.41084373 | 5 / -6.49223042 | False | False | False | False |
### multi-backup

Compare North and South backup frequency and retention.

Expected evidence: [{'document': 'operations-guide.pdf', 'page': 1}, {'document': 'operations-guide.pdf', 'page': 4}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p4–4 | 1 / 0.58553876 | 1 / 4.42093945 | True | True | False | True |
| operations-guide.pdf p1–1 | 2 / 0.49540719 | 2 / -0.18945040 | True | True | False | False |
| web-guide.pdf p2–2 | 3 / 0.24297700 | 3 / -7.86797190 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.20383999 | 4 / -10.58650494 | False | False | False | False |
| data-guide.pdf p3–3 | 5 / 0.19709113 | 5 / -11.49235249 | False | False | False | False |
### unsupported-refresh

How long are refresh tokens stored?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p2–2 | 2 / 0.56038844 | 1 / 0.77108312 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.40537517 | 2 / -2.62198162 | False | False | False | False |
| operations-guide.pdf p2–2 | 3 / 0.42335443 | 3 / -3.57821703 | False | False | False | False |
| operations-guide.pdf p1–1 | 5 / 0.36208191 | 4 / -6.58924294 | False | False | False | False |
| web-guide.pdf p2–2 | 1 / 0.57503199 | 5 / -6.89961720 | False | False | False | False |
### unsupported-rotation

How often does this server rotate its JWT signing secret?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p1–1 | 1 / 0.51616776 | 1 / 1.14804173 | False | False | False | False |
| operations-guide.pdf p3–3 | 2 / 0.43592272 | 2 / -3.77159882 | False | False | False | False |
| operations-guide.pdf p2–2 | 3 / 0.39635071 | 3 / -4.10796499 | False | False | False | False |
| authentication-guide.pdf p2–2 | 4 / 0.37295361 | 4 / -11.13686562 | False | False | False | False |
| web-guide.pdf p2–2 | 5 / 0.33616833 | 5 / -11.40189743 | False | False | False | False |
### unsupported-region

Which geographic region hosts the MongoDB cluster?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| authentication-guide.pdf p4–4 | 3 / 0.37122503 | 1 / -6.12153721 | False | False | False | False |
| data-guide.pdf p1–1 | 1 / 0.44434452 | 2 / -6.29424000 | False | False | False | False |
| storage-guide.pdf p1–1 | 2 / 0.41171932 | 3 / -6.96337032 | False | False | False | False |
| storage-guide.pdf p3–3 | 4 / 0.26865392 | 4 / -8.57859993 | False | False | False | False |
| data-guide.pdf p2–2 | 5 / 0.12897785 | 5 / -11.38759422 | False | False | False | False |
### unsupported-price

What monthly price does the PaaS provider charge?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| cloud-guide.pdf p1–1 | 3 / 0.12860814 | 1 / -11.19815445 | False | False | False | False |
| web-guide.pdf p3–3 | 1 / 0.18819380 | 2 / -11.25690269 | False | False | False | False |
| cloud-guide.pdf p3–3 | 2 / 0.17039699 | 3 / -11.28863239 | False | False | False | False |
| authentication-guide.pdf p3–3 | 5 / 0.09274698 | 4 / -11.30540848 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.09403967 | 5 / -11.31619263 | False | False | False | False |
### unsupported-recovery

What is the measured recovery time after a North outage?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p2–2 | 3 / 0.27449171 | 1 / -3.90649986 | False | False | False | False |
| operations-guide.pdf p1–1 | 1 / 0.44002135 | 2 / -4.74019718 | False | False | False | False |
| operations-guide.pdf p4–4 | 2 / 0.34379051 | 3 / -7.01800346 | False | False | False | False |
| authentication-guide.pdf p2–2 | 5 / 0.19668879 | 4 / -11.11497784 | False | False | False | False |
| web-guide.pdf p3–3 | 4 / 0.22039307 | 5 / -11.41096497 | False | False | False | False |
### unrelated-rice

When should rice fields be irrigated?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p1–1 | 1 / 0.15710584 | 1 / -11.34356117 | False | False | False | False |
| operations-guide.pdf p4–4 | 3 / 0.11921649 | 2 / -11.38196850 | False | False | False | False |
| cloud-guide.pdf p1–1 | 2 / 0.12189094 | 3 / -11.40673828 | False | False | False | False |
| operations-guide.pdf p3–3 | 4 / 0.09992295 | 4 / -11.41880226 | False | False | False | False |
| web-guide.pdf p2–2 | 5 / 0.06197089 | 5 / -11.42874336 | False | False | False | False |
### unrelated-bread

How much yeast should I add to sourdough bread?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p1–1 | 1 / 0.10990535 | 1 / -11.32901192 | False | False | False | False |
| operations-guide.pdf p4–4 | 4 / 0.07029532 | 2 / -11.34005737 | False | False | False | False |
| authentication-guide.pdf p4–4 | 2 / 0.10464543 | 3 / -11.35102654 | False | False | False | False |
| web-guide.pdf p3–3 | 3 / 0.07634074 | 4 / -11.35579300 | False | False | False | False |
| storage-guide.pdf p1–1 | 5 / 0.01065771 | 5 / -11.35697174 | False | False | False | False |
### unrelated-moon

How many moons orbit Neptune?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| cloud-guide.pdf p2–2 | 3 / 0.06006256 | 1 / -11.38323212 | False | False | False | False |
| operations-guide.pdf p4–4 | 1 / 0.08359429 | 2 / -11.40595818 | False | False | False | False |
| data-guide.pdf p2–2 | 4 / 0.05957766 | 3 / -11.42753601 | False | False | False | False |
| web-guide.pdf p3–3 | 2 / 0.06300842 | 4 / -11.42841625 | False | False | False | False |
| data-guide.pdf p1–1 | 5 / 0.04769741 | 5 / -11.43809509 | False | False | False | False |
### unrelated-piano

How should I tune a grand piano?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p4–4 | 3 / -0.01031443 | 1 / -11.29588318 | False | False | False | False |
| operations-guide.pdf p1–1 | 1 / 0.05855029 | 2 / -11.31307411 | False | False | False | False |
| cloud-guide.pdf p2–2 | 5 / -0.04704909 | 3 / -11.31644058 | False | False | False | False |
| data-guide.pdf p2–2 | 2 / 0.03324994 | 4 / -11.34460831 | False | False | False | False |
| cloud-guide.pdf p3–3 | 4 / -0.01604807 | 5 / -11.35318565 | False | False | False | False |
### unrelated-bird

Why do swallows migrate south?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p4–4 | 2 / 0.13324219 | 1 / -11.27678490 | False | False | False | False |
| operations-guide.pdf p3–3 | 1 / 0.15400278 | 2 / -11.33892822 | False | False | False | False |
| authentication-guide.pdf p3–3 | 4 / 0.07182296 | 3 / -11.37738419 | False | False | False | False |
| data-guide.pdf p3–3 | 3 / 0.08680548 | 4 / -11.40027237 | False | False | False | False |
| storage-guide.pdf p1–1 | 5 / 0.06778449 | 5 / -11.43468857 | False | False | False | False |
### ambiguous-expiration

When does it expire?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p3–3 | 3 / 0.27208015 | 1 / 1.75969195 | False | False | False | False |
| authentication-guide.pdf p2–2 | 1 / 0.45293337 | 2 / 1.17077124 | False | False | False | False |
| web-guide.pdf p2–2 | 2 / 0.35572368 | 3 / -1.69065893 | False | False | False | False |
| operations-guide.pdf p2–2 | 4 / 0.24983526 | 4 / -4.31168365 | False | False | False | False |
| operations-guide.pdf p1–1 | 5 / 0.18823683 | 5 / -10.51062107 | False | False | False | False |
### ambiguous-best

Which service is the best?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p4–4 | 3 / 0.22747907 | 1 / -4.58397293 | False | False | False | False |
| cloud-guide.pdf p1–1 | 2 / 0.24954985 | 2 / -5.51025963 | False | False | False | False |
| cloud-guide.pdf p3–3 | 1 / 0.32897553 | 3 / -6.19059181 | False | False | False | False |
| cloud-guide.pdf p2–2 | 5 / 0.19012439 | 4 / -6.74380159 | False | False | False | False |
| web-guide.pdf p3–3 | 4 / 0.19500522 | 5 / -10.57102394 | False | False | False | False |
### ambiguous-safety

Is everything secure?

Expected evidence: []. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| web-guide.pdf p1–1 | 5 / 0.06351647 | 1 / -9.19072151 | False | False | False | False |
| authentication-guide.pdf p1–1 | 1 / 0.20144689 | 2 / -10.08710384 | False | False | False | False |
| authentication-guide.pdf p3–3 | 2 / 0.14945864 | 3 / -10.78806114 | False | False | False | False |
| cloud-guide.pdf p1–1 | 3 / 0.12326025 | 4 / -10.94655895 | False | False | False | False |
| cloud-guide.pdf p2–2 | 4 / 0.07524178 | 5 / -11.03997135 | False | False | False | False |
### ambiguous-data

Where is application metadata stored?

Expected evidence: [{'document': 'data-guide.pdf', 'page': 1}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| data-guide.pdf p1–1 | 1 / 0.47693742 | 1 / 5.80159473 | True | True | True | True |
| storage-guide.pdf p1–1 | 2 / 0.35370438 | 2 / -0.67882252 | False | True | True | False |
| operations-guide.pdf p4–4 | 4 / 0.30025573 | 3 / -6.90716934 | False | False | False | False |
| operations-guide.pdf p1–1 | 5 / 0.28046075 | 4 / -7.81101418 | False | False | False | False |
| cloud-guide.pdf p2–2 | 3 / 0.32550393 | 5 / -10.09408665 | False | False | False | False |
### ambiguous-remember

Does the next question use my past answers?

Expected evidence: [{'document': 'data-guide.pdf', 'page': 3}]. Accepted: False; complete: False.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| data-guide.pdf p3–3 | 1 / 0.34939177 | 1 / -3.66167355 | True | False | False | False |
| data-guide.pdf p1–1 | 2 / 0.14381718 | 2 / -9.53986073 | False | False | False | False |
| web-guide.pdf p2–2 | 3 / 0.13214717 | 3 / -11.41916466 | False | False | False | False |
| operations-guide.pdf p4–4 | 4 / 0.12910506 | 4 / -11.44806099 | False | False | False | False |
| operations-guide.pdf p1–1 | 5 / 0.12112999 | 5 / -11.44903755 | False | False | False | False |
### disambig-session

How long can the North dashboard sit idle before closing its session?

Expected evidence: [{'document': 'operations-guide.pdf', 'page': 2}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p2–2 | 1 / 0.71190484 | 1 / 8.76603794 | True | True | False | True |
| operations-guide.pdf p1–1 | 3 / 0.26967900 | 2 / -3.96330118 | False | False | False | False |
| operations-guide.pdf p4–4 | 4 / 0.24921819 | 3 / -6.83298969 | False | False | False | False |
| authentication-guide.pdf p2–2 | 2 / 0.31303162 | 4 / -10.35244179 | False | False | False | False |
| web-guide.pdf p3–3 | 5 / 0.24129430 | 5 / -11.16279984 | False | False | False | False |
### disambig-api

When does a South automation API key expire?

Expected evidence: [{'document': 'operations-guide.pdf', 'page': 3}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p3–3 | 1 / 0.75694749 | 1 / 8.47389984 | True | True | False | True |
| authentication-guide.pdf p2–2 | 2 / 0.42585600 | 2 / -4.83725071 | False | False | False | False |
| operations-guide.pdf p2–2 | 5 / 0.34843261 | 3 / -7.10243034 | False | False | False | False |
| web-guide.pdf p2–2 | 4 / 0.39457418 | 4 / -8.70311928 | False | False | False | False |
| operations-guide.pdf p4–4 | 3 / 0.39769757 | 5 / -10.07711220 | False | False | False | False |
### disambig-south

How many days does South retain metadata backups?

Expected evidence: [{'document': 'operations-guide.pdf', 'page': 4}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| operations-guide.pdf p4–4 | 1 / 0.82214390 | 1 / 9.22791576 | True | True | False | True |
| operations-guide.pdf p1–1 | 2 / 0.66083170 | 2 / 5.84856224 | False | True | False | True |
| operations-guide.pdf p3–3 | 3 / 0.36631860 | 3 / -1.00685859 | False | False | False | False |
| web-guide.pdf p2–2 | 4 / 0.32793317 | 4 / -8.54060841 | False | False | False | False |
| storage-guide.pdf p1–1 | 5 / 0.30659507 | 5 / -10.77635098 | False | False | False | False |
### disambig-csrf

Which token protects browser forms from cross-site request forgery?

Expected evidence: [{'document': 'web-guide.pdf', 'page': 1}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| web-guide.pdf p1–1 | 1 / 0.66394385 | 1 / 10.34011841 | True | True | False | True |
| authentication-guide.pdf p1–1 | 2 / 0.44614254 | 2 / -4.95582771 | False | False | False | False |
| authentication-guide.pdf p2–2 | 3 / 0.39155080 | 3 / -7.04877663 | False | False | False | False |
| operations-guide.pdf p2–2 | 5 / 0.29080030 | 4 / -7.70465469 | False | False | False | False |
| web-guide.pdf p2–2 | 4 / 0.32017692 | 5 / -10.08966637 | False | False | False | False |
### disambig-rate

How many requests per minute does the public status endpoint allow?

Expected evidence: [{'document': 'web-guide.pdf', 'page': 3}]. Accepted: True; complete: True.

| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |
|---|---|---|---|---|---|---|
| web-guide.pdf p3–3 | 1 / 0.79043850 | 1 / 9.93531799 | True | True | False | True |
| authentication-guide.pdf p2–2 | 2 / 0.35215680 | 2 / -8.64113426 | False | False | False | False |
| operations-guide.pdf p2–2 | 3 / 0.27116515 | 3 / -10.74459839 | False | False | False | False |
| operations-guide.pdf p1–1 | 5 / 0.18164878 | 4 / -11.36018562 | False | False | False | False |
| web-guide.pdf p2–2 | 4 / 0.23731989 | 5 / -11.46268272 | False | False | False | False |

## All thirteen unsupported cases

| Case | Original max cosine | Max CE | Accepted | Existing FP corrected | New FP |
|---|---|---|---|---|---|
| unsupported-refresh | 0.57503199 | 0.77108312 | False | True | False |
| unsupported-rotation | 0.51616776 | 1.14804173 | False | True | False |
| unsupported-region | 0.44434452 | -6.12153721 | False | False | False |
| unsupported-price | 0.18819380 | -11.19815445 | False | False | False |
| unsupported-recovery | 0.44002135 | -3.90649986 | False | False | False |
| unrelated-rice | 0.15710584 | -11.34356117 | False | False | False |
| unrelated-bread | 0.10990535 | -11.32901192 | False | False | False |
| unrelated-moon | 0.08359429 | -11.38323212 | False | False | False |
| unrelated-piano | 0.05855029 | -11.29588318 | False | False | False |
| unrelated-bird | 0.15400278 | -11.27678490 | False | False | False |
| ambiguous-expiration | 0.45293337 | 1.75969195 | False | False | False |
| ambiguous-best | 0.32897553 | -4.58397293 | False | False | False |
| ambiguous-safety | 0.20144689 | -9.19072151 | False | False | False |

## Separate safety set — checked after selection

Baseline: `{'tp': 3, 'tn': 5, 'fp': 1, 'fn': 1, 'precision': 0.75, 'recall': 0.75, 'f1': 0.75}`; complete 2/4.

Comparator: `{'tp': 2, 'tn': 6, 'fp': 0, 'fn': 2, 'precision': 1.0, 'recall': 0.5, 'f1': 0.6666666666666666}`; complete 2/4. No thresholds changed after this check.

| Case/question | Max cosine | Max CE | Accepted | Complete | New FP |
|---|---|---|---|---|---|
| safety-related-signature: How does JWT authentication work and why is its signature checked? | 0.78077547 | 5.71953630 | True | True | False |
| safety-three-topics: Explain JWT authentication, MongoDB storage, and cloud service models. | 0.54308439 | -1.99215901 | False | False | False |
| safety-unsupported-rotation: Explain JWT signing-key rotation and emergency key revocation. | 0.49870881 | -2.49757504 | False | False | False |
| safety-unrelated-conjunction: How do irrigation and fertilizer improve rice farming? | 0.15741285 | -11.28160572 | False | False | False |
| safety-single-concept: Research and development | 0.23648274 | -10.65991211 | False | False | False |
| safety-explicit-questions: Where are original PDFs stored? How long do access tokens remain valid? | 0.52362345 | 3.33694196 | True | True | False |
| safety-semicolon-unsupported: How long are refresh tokens stored; what is the JWT signing-key rotation policy? | 0.52263948 | 0.83429599 | False | False | False |
| safety-mixed-supported-unsupported: Explain JWT signatures and rice irrigation. | 0.45187106 | -0.52302432 | False | False | False |
| safety-comparison-unsupported: Compare what the customer manages in IaaS and the lunar habitat. | 0.26397895 | -8.52693653 | False | False | False |
| safety-semicolon-supported: Where is application metadata stored; which algorithm stores password hashes? | 0.45132195 | 1.48950362 | False | False | False |

## Boundaries and limitations

Exploratory thresholds selected on the same small synthetic development benchmark; not held-out accuracy.
Safety cases are separate checks, not threshold-training inputs; a positive passage does not establish support for every question part.
Non-required is a ground-truth proxy, not proof of irrelevance. Model relevance is not factual confidence or entailment.
Local batched throughput is not production latency; no Gemini answers or production services were evaluated.

Historical reports and production settings are unchanged. Citations remain assigned after final evidence selection by the trusted server map. No production reranker, Phase16B implementation, model replacement, decomposition, hybrid/BM25, LLM judge, or Phase17 work.

Model-free replay: `python -B backend/evaluation/phase16r.py`. Explicit local-only measurement: add `--measure`; independent offline repeat: add `--verify-offline`. Download is a separate explicit adapter command, never an inference fallback. Timing is stored separately; metric replay uses recorded values and is deterministic.

## Evidence and distractor interpretation

- **scope**: Recommendation concerns this model and bounded tested policies, not all possible rerankers or relevance policies.
- **single_vs_dual**: At gate 3, evidence 3 or 1 retains 12/22 complete and 0/5 multi; evidence -1 restores one backup page, reaching 13/22 and 1/5. Two thresholds help but do not satisfy adoption.
- **recall_tradeoff**: Gate -6 / evidence -10 reaches 18/22 complete and 4/5 multi, but TP/TN/FP/FN becomes 19/8/5/3, precision 0.79167, and neither existing FP is corrected. Evidence retention alone is not sufficient.
- **new_false_negative**: multi-storage was baseline-answerable but its strongest reranker score is -0.79987; the selected diagnostic gate rejects it. Two recovered old FNs therefore yield only one net additional TP.
- **useful_ranking**: para-history required page moves 2 to 1, but -6.94406 still fails the comparator gate. Promotion is not the same as recovered answerability.
- **harmful_ranking**: para-signature required signature page moves 1 to 3 with -9.81566; expiration and CSRF passages outrank the integrity evidence. This reduces MRR even though overall Hit@1 is unchanged.
- **false_positive_correction**: unsupported-refresh originally ranks web cache page first; CE demotes it 1 to 5 (-6.89962) but promotes access-token expiry to rank 1 (0.77108). unsupported-rotation keeps signature verification first (1.14804). Gate 3 rejects both; neither passage establishes the absent refresh-retention or rotation policy.
- **multi_page_loss**: multi-token loses required signature evidence (about -9.44) despite accepting expiry (about 3.01); multi-deletion loses history snapshot evidence (about -6.34) despite accepting deletion (about 3.70). multi-cloud ranks PaaS better (5 to 3), yet its required score is about -11.00.
- **new_related_context**: ambiguous-data newly includes storage-guide.pdf page 1 (-0.67882), describing physical PDFs and MongoDB metadata. This is useful related background beyond the labeled data-guide page; non-required is not automatically misleading.
- **budget**: All fixed synthetic pairs are at most 65 tokens, below 512; no truncation or 12000-character budget exclusion explains these evidence losses.
- **safety**: Frozen policy rejects mixed JWT/irrigation and unsupported rotation/revocation, but also rejects the answerable three-topic question. No policy was retuned on safety results.
