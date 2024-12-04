FROM golang:1.23-alpine AS builder
#
ENV GO111MODULE=on \
    CGO_ENABLED=0 \
    GOOS=linux
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U add \
		build-base \
		git \
		g++
#
# Setup go, build dicompot 
RUN git clone https://github.com/nsmfoo/dicompot.git && \
    cd dicompot && \
    git checkout 41331194156bbb17078bcc1594f4952ac06a731e && \
    cp /root/dist/go.mod .
WORKDIR /go/dicompot
RUN go mod tidy
RUN go mod download
RUN go build -o dicompot github.com/nsmfoo/dicompot/server
#
FROM scratch
#
COPY --from=builder /go/dicompot/dicompot /opt/dicompot/dicompot
COPY --from=builder /root/dist/dcm_pts/images /opt/dicompot/images
WORKDIR /opt/dicompot
USER 2000:2000
CMD ["-ip","0.0.0.0","-dir","images","-log","/var/log/dicompot/dicompot.log"]
ENTRYPOINT ["./dicompot"]
