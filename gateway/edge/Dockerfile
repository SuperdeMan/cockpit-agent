# build context 为项目根。需先 `make proto` 生成 gen/go。
FROM golang:1.22 AS build
WORKDIR /src
COPY go.mod ./
COPY gen/go ./gen/go
COPY gateway ./gateway
RUN go mod tidy && go build -o /out/edge-gateway ./gateway/edge

FROM gcr.io/distroless/base-debian12
COPY --from=build /out/edge-gateway /edge-gateway
EXPOSE 8090
ENTRYPOINT ["/edge-gateway"]
