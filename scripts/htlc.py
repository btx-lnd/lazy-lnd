import grpc
from drivers import router_pb2
from drivers import router_pb2_grpc
import codecs
import argparse
from google.protobuf.json_format import MessageToDict
import json


def metadata_callback(context, callback):
    macaroon_path = "/app/config/admin.macaroon"
    with open(macaroon_path, "rb") as f:
        macaroon_bytes = f.read()
    macaroon = codecs.encode(macaroon_bytes, "hex")
    callback([("macaroon", macaroon)], None)


def get_secure_channel():
    cert_path = "/app/config/tls.cert"
    with open(cert_path, "rb") as f:
        cert = f.read()
    creds = grpc.ssl_channel_credentials(cert)
    auth_creds = grpc.metadata_call_credentials(metadata_callback)
    combined_creds = grpc.composite_channel_credentials(creds, auth_creds)
    channel = grpc.secure_channel("lnd:10009", combined_creds)
    return channel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--failed-only", action="store_true", help="Only log failed HTLC events"
    )
    args = parser.parse_args()

    channel = get_secure_channel()
    client = router_pb2_grpc.RouterStub(channel)
    request = router_pb2.SubscribeHtlcEventsRequest()
    event_stream = client.SubscribeHtlcEvents(request)

    print("Subscribed to HTLC events...")
    log_path = "/app/log/htlc.ndjson"
    with open(log_path, "a") as logfile:
        for event in event_stream:
            event_dict = MessageToDict(event, preserving_proto_field_name=True)
            if "final_htlc_event" in event_dict:
                continue

            is_failed = (
                "forward_fail_event" in event_dict
                or "link_fail_event" in event_dict
                or "failure" in event_dict
            )
            if args.failed_only and not is_failed:
                continue  # skip non-failures
            print(event_dict)
            json.dump(event_dict, logfile, separators=(",", ":"))
            logfile.write("\n")
            logfile.flush()


if __name__ == "__main__":
    main()
