"""Optional same-origin routing qualification; no network or native account actions."""
import unittest
from unittest.mock import patch
from jev_luna import native_rpc, subscription_auth

class RegionalTests(unittest.TestCase):
    def test_header_and_origin_are_authoritative_and_bounded(self):
        for mode in ("NO_CONSTRAINT", "us", "us_cr"):
            st={"requiresOpenaiAuth":True,"authMethod":"chatgpt","authToken":"t"}
            ac={"requiresOpenaiAuth":True,"account":{"type":"chatgpt"},
                "workspaceRouting":{"chatgptAccountId":"A","backendOrigin":"https://chatgpt.com","accountRoutingOverride":mode}}
            value=native_rpc.validate_pair(st,ac)
            with patch.object(subscription_auth,"_credentials",return_value=("t","A",value.generation)), \
                 patch.object(subscription_auth,"getproxies",return_value={}):
                conn,path,headers=subscription_auth.connection(1)
            try:
                self.assertEqual(conn.host,"chatgpt.com")
                self.assertEqual(path,"/backend-api/codex/responses")
                self.assertEqual(headers.get("x-openai-account-routing-override"),None if mode=="NO_CONSTRAINT" else mode)
            finally:conn.close()
            for origin in ("https://gov.chatgpt.com","https://evil.invalid","http://chatgpt.com","https://chatgpt.com:444"):
                ac["workspaceRouting"]["backendOrigin"]=origin
                with self.assertRaises(native_rpc.NativeRpcError):native_rpc.validate_pair(st,ac)

if __name__=="__main__":unittest.main()
