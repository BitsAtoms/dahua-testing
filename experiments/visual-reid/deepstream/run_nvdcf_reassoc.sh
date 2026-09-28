#!/usr/bin/env bash
set -euo pipefail

official_tracker=/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_accuracy.yml
tuned_tracker=/tmp/config_tracker_NvDCF_reassoc.yml
tuned_app=/tmp/deepstream_nvdcf_reassoc.txt
primary_gie_config=${PRIMARY_GIE_CONFIG:-/workspace/config/config_infer_peoplenet_transformer.txt}
output_stem=${OUTPUT_STEM:-nvdcf-reassoc}

cp "$official_tracker" "$tuned_tracker"
sed -i \
  -e 's/maxShadowTrackingAge: 42/maxShadowTrackingAge: 180/' \
  -e 's/maxTrackletMatchingTimeSearchRange: 27/maxTrackletMatchingTimeSearchRange: 180/' \
  -e 's/reidExtractionInterval: 8/reidExtractionInterval: 2/' \
  "$tuned_tracker"

cp /workspace/config/deepstream_nvdcf.txt "$tuned_app"
sed -i \
  -e "s|gie-kitti-output-dir=.*|gie-kitti-output-dir=/workspace/output/kitti-$output_stem|" \
  -e "s|kitti-track-output-dir=.*|kitti-track-output-dir=/workspace/output/kitti-track-$output_stem-person|" \
  -e "s|config-file=.*|config-file=$primary_gie_config|" \
  -e "s|ll-config-file=.*|ll-config-file=$tuned_tracker|" \
  "$tuned_app"

exec deepstream-app -c "$tuned_app"
