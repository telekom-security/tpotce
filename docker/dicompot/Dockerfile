FROM golang:1.26-alpine AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U add \
		git
#
# Setup go, build dicompot 
RUN git clone https://github.com/nsmfoo/dicompot.git && \
    cd dicompot && \
    git checkout 7351dbec0d3ac66986912fe88710f99c17004188
WORKDIR /go/dicompot
ENV GOCACHE=/go-cache
ENV GOMODCACHE=/gomod-cache
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
