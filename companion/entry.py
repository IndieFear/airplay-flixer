import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--autosync-worker':
        from airplay_flixer.autosync import worker_main
        worker_main(sys.argv[2])
    elif sys.argv[1:] == ['--check-power']:
        from airplay_flixer.power import SleepGuard
        guard = SleepGuard()
        try:
            guard.acquire()
            assert guard.identifier is not None
        finally:
            guard.release()
        assert guard.identifier is None
        print('macOS idle sleep protection: OK')
    elif sys.argv[1:] == ['--check-autosync']:
        from airplay_flixer.autosync import self_check
        self_check()
    elif sys.argv[1:] == ['--check-pairing']:
        from pyatv.auth.hap_srp import SRPAuthHandler
        auth = SRPAuthHandler()
        auth.initialize()
        auth.step1('1234')
        public, proof = auth.step2(bytes([3]) * 384, bytes([2]) * 16)
        assert public and len(proof) == 64
        print('SRP pairing compatibility: OK')
    elif len(sys.argv) > 1 and sys.argv[1].startswith("chrome-extension://"):
        from airplay_flixer.native_host import main
        main(sys.argv[1])
    else:
        from airplay_flixer.server import main
        main()
