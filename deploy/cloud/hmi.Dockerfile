FROM node:20-slim
WORKDIR /app

COPY hmi/package.json ./
RUN npm install
COPY hmi/ .

# These files are ignored build artifacts. The cloud release pipeline validates
# every SHA-256 before this named context is exposed to BuildKit. Keep the copy
# list exact so unrelated local models can never leak into the HMI image.
COPY --from=hmi_runtime_models public/models/silero_vad.onnx /app/public/models/silero_vad.onnx
COPY --from=hmi_runtime_models public/kws/sherpa-onnx-kws.js /app/public/kws/sherpa-onnx-kws.js
COPY --from=hmi_runtime_models public/kws/sherpa-onnx-wasm-kws-main.data /app/public/kws/sherpa-onnx-wasm-kws-main.data
COPY --from=hmi_runtime_models public/kws/sherpa-onnx-wasm-kws-main.js /app/public/kws/sherpa-onnx-wasm-kws-main.js
COPY --from=hmi_runtime_models public/kws/sherpa-onnx-wasm-kws-main.wasm /app/public/kws/sherpa-onnx-wasm-kws-main.wasm

EXPOSE 5173
CMD ["npm", "run", "dev"]
