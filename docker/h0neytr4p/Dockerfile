FROM golang:1.26.4-alpine3.23 AS builder
WORKDIR /app
#
RUN apk -U upgrade
RUN apk -U add ca-certificates git openssl
RUN git clone https://github.com/t3chn0m4g3/h0neytr4p -b v0.42 /app
RUN go mod download
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/h0neytr4p .
RUN openssl req \
          -nodes \
          -x509 \
          -sha512 \
          -newkey rsa:4096 \
          -keyout "/out/app.key" \
          -out "/out/app.crt" \
          -days 3650 \
          -subj '/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd'
RUN chmod 444 /out/app.key /out/app.crt
#
FROM scratch
#
COPY --from=builder /out/h0neytr4p /opt/h0neytr4p/h0neytr4p
COPY --from=builder /app/traps /opt/h0neytr4p/traps
COPY --from=builder /out/app.key /opt/h0neytr4p/
COPY --from=builder /out/app.crt /opt/h0neytr4p/
#
WORKDIR /opt/h0neytr4p
USER 2000:2000
CMD ["-cert=app.crt", "-key=app.key", "-log=log/log.json", "-catchall=false", "-payload=/data/h0neytr4p/payloads/", "-wildcard=true", "-traps=traps/"]
ENTRYPOINT ["./h0neytr4p"]
