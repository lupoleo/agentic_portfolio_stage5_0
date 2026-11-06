from app.scanner.providers.borsa_italiana_ftsemib import BorsaItalianaFTSEMIBProvider, BorsaItalianaProviderStatus

def main():
    r=BorsaItalianaFTSEMIBProvider().fetch()
    print(f'provider: {r.provider_id} v{r.provider_version}')
    print(f'status: {r.status.value}')
    print(f"membership references: {r.metadata.get('membership_reference_count',0)}")
    print(f"resolved listings: {r.metadata.get('resolved_listing_count',0)}")
    print(f"rejected details: {r.metadata.get('rejected_detail_count',0)}")
    if r.diagnostics:
        print('diagnostics:')
        for d in r.diagnostics: print(f"  {d.code}{' ['+d.isin+']' if d.isin else ''}: {d.message}")
    if r.status is BorsaItalianaProviderStatus.FAILED: return 1
    print('\nFTSE MIB listings:')
    for x in r.listings: print(f"  {x.symbol:12} {x.isin or '-':12} {x.name or '-'}")
    return 0 if r.status is BorsaItalianaProviderStatus.SUCCESS else 2
if __name__=='__main__': raise SystemExit(main())
