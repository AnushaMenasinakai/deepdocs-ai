ARG REFERENCE_IMAGE
FROM ${REFERENCE_IMAGE}
USER root
ENV PYTHONPATH=/app/backend
# Experiment-only dependencies; production dependency specifications are untouched.
RUN python -c "import importlib.metadata as m,json; open('/tmp/reference-packages.json','w').write(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))" \
 && python -m pip install --only-binary=:all: onnx==1.20.1 onnxruntime==1.24.2 \
 && python -m pip check \
 && python -c "import importlib.metadata as m,json; prior=json.load(open('/tmp/reference-packages.json')); assert all(m.version(k)==v for k,v in prior.items()), 'Experiment changed reference dependencies'" \
 && rm /tmp/reference-packages.json
COPY export.py /experiment/export.py
RUN --network=none python /experiment/export.py && chmod -R a+rX,a-w /experiment/model
COPY probe.py /experiment/probe.py
USER 10001:10001
ENTRYPOINT ["python", "/experiment/probe.py"]
CMD []
