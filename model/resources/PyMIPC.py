#!/usr/bin/env python3
## @file PyMIPC.py
# this module imports the c library used for MIPC communication and wraps those functions

from ctypes import *
import os
import sys


WINLIB = r"C:\mu\bin\cmipc100x32.dll"
LINLIB = '/mu/mtsdk/corplib/gcc422/lib/libmipc.so'


if sys.maxsize > 2**32:
    WINLIB = r"C:\mu\bin64\cmipc100x64.dll"
    LINLIB = '/mu/mtsdk/corplib/gcc443-rhel5-64/lib/libmipc.so'


class MIPC(object):
    def __init__(self, site=None):
        self.site = None
        if not site is None:
            self.site = site
        else:
            try:
                self.site = os.environ["SITE_NAME"]
            except:
                pass

        bWin = False
        try:
            #initialize mipc
            if os.name == 'nt':
                bWin = True
                self.mipclib = windll.LoadLibrary(WINLIB)
            else:
                self.mipclib = cdll.LoadLibrary(LINLIB)
        except:
            if bWin:
                raise Exception("The required MIPC C Library was not found. %s" %WINLIB)
            else:
                raise Exception("The required MIPC C Library was not found. %s" %LINLIB)

        if self.site is None:
            self.mipclib.MIPC_init()
        else:
            self.mipclib.MIPC_init_with_site(self.site.encode('utf-8'))

    def __del__(self):
        self.mipclib.MIPC_deinit()


    ## method SendReceive: Provides synchronous operation for send and receive through mipc\n
    # parameter sDest - the destination address\n
    # parameter sMsg - the message to send\n
    # parameter nTimeout - the amount of time to wait in seconds before throwing a timeout error\n
    # parameter retMessageSize - the expected size of the return message\n
    # return int, string\n
    def SendReceive(self, sDest, sMsg, nTimeout=30, retMessageSize=50000):
        retVal = create_string_buffer(retMessageSize)
        status = self.mipclib.MIPC_send_receive(sDest.encode('utf-8'), sMsg.encode('utf-8'), len(sMsg), byref(retVal), retMessageSize, 0, nTimeout)
        if status <= 0: # we have an error
            lErrorCode = c_uint()
            self.mipclib.MIPC_return_status(byref(lErrorCode), retVal, retMessageSize)
            return lErrorCode.value, retVal

        return status, retVal.value


    ## method SendReceive: Provides send method for one way communication\n
    # parameter sFrom - the source address\n
    # parameter sDest - the destination address\n
    # parameter sReplyAddr - the reply address (usually the same as from)\n
    # parameter sMsg - the message to send\n
    # parameter nTimeout - the amount of time to wait in seconds before throwing a timeout error\n
    def Send(self, sFrom, sDest, sReplyAddr, sMsg, nTimeout=30):
        status = self.mipclib.MIPC_send(sFrom.encode('utf-8'), sDest.encode('utf-8'), sReplyAddr.encode('utf-8'), sMsg.encode('utf-8'), len(sMsg), 0)
        retVal = create_string_buffer(10000)
        retVal.value = b"SUCCESS"
        if status <= 0: # we have an error
            lErrorCode = c_uint()
            self.mipclib.MIPC_return_status(byref(lErrorCode), retVal, 10000)
            return lErrorCode.value, retVal

        return status, retVal.value




if __name__ == '__main__':
    msg = '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope"><SOAP-ENV:Header><MTXMLMsg MsgType="CMD" /><Delivery><Dest>/BOISE_2/MTI/MFG/MAM/PROD/MAMSRV/MOD_XML</Dest><Src>/BOISE/MTI/SIG/MTO/RLONSETH</Src><Reply>/BOISE/MTI/SIG/MTO/RLONSETH</Reply><TransportType>MIPC</TransportType></Delivery></SOAP-ENV:Header><SOAP-ENV:Body><GetMAInfo><MAId>BZAHG3N003</MAId><MAType>MODULE LOT</MAType><ExecutionFacility>MODULE ASSEMBLY</ExecutionFacility><MAAttrAll /><Authorization><ClientId>MODULETESTOBJECT</ClientId></Authorization></GetMAInfo></SOAP-ENV:Body></SOAP-ENV:Envelope>'
    mipc = MIPC()
    status, retMsg = mipc.SendReceive('/BOISE_2/MTI/MFG/MAM/PROD/MAMSRV/MOD_XML', msg)
    #status, retMsg = mipc.Send('/BOISE/MTI/SIG/MTO/RLONSETH', '/BOISE_2/MTI/MFG/MAM/PROD/MAMSRV/MOD_XML', '/BOISE/MTI/SIG/MTO/RLONSETH', msg)

    print(retMsg)


