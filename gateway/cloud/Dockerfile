# build context 为项目根。需先 `make proto` 生成 gen/go。
FROM golang:1.24 AS build
# 国内网络拉 proxy.golang.org / sum.golang.org 不稳（unexpected EOF）→ 走国内镜像、关 sumdb 校验。
ENV GOPROXY=https://goproxy.cn,direct \
    GOSUMDB=off
WORKDIR /src
COPY go.mod ./
COPY gen/go ./gen/go
COPY gateway ./gateway
RUN go mod tidy && go build -o /out/cloud-gateway ./gateway/cloud

FROM gcr.io/distroless/base-debian12
COPY --from=build /out/cloud-gateway /cloud-gateway
EXPOSE 8080
ENTRYPOINT ["/cloud-gateway"]
