@echo off
setlocal
set "INTENTRA_MODEL_DIR=%~dp0models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
"%~dp0Intentra.exe" %*
