#!/usr/bin/env bash
set -euo pipefail

official_tracker=/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_accuracy.yml
tuned_tracker=/tmp/config_tracker_NvDCF_reassoc.yml
tuned_app=/tmp/deepstream_nvdcf_reassoc.txt

cp "$official_tracker" "$tuned_tracker"
sed -i \
  -e 's/maxShadowTrackingAge: 42/maxShadowTrackingAge: 180/' \
  -e 's/maxTrackletMatchingTimeSearchRange: 27/maxTrackletMatchingTimeSearchRange: 180/' \
  -e 's/reidExtractionInterval: 8/reidExtractionInterval: 2/' \
  "$tuned_tracker"

cp /workspace/config/deepstream_nvdcf.txt "$tuned_app"
sed -i \
  -e 's|kitti-track-nvdcf-person|kitti-track-nvdcf-reassoc-person|' \
  -e 's|kitti-nvdcf|kitti-nvdcf-reassoc|g' \
  -e "s|ll-config-file=.*|ll-config-file=$tuned_tracker|" \
  "$tuned_app"

exec deepstream-app -c "$tuned_app"
