from app.scanner.providers.borsa_italiana_ftsemib import BorsaItalianaFTSEMIBProvider, BorsaItalianaProviderStatus, parse_constituent_page, parse_instrument_detail

def membership_html(items):
    return '<html><body>' + ''.join(f'<a class="x" href="/borsa/azioni/scheda/{isin}-MTAA.html?lang=it">{name}</a>' for isin,name in items) + '</body></html>'
def detail_html(isin,symbol,market='Euronext Milan'):
    return f'<html><body><span>Mercato/Segmento</span><b>{market}</b><span>Codice Isin</span><b>{isin}</b><span>Codice Alfanumerico</span><b>{symbol}</b></body></html>'
class FakeResponse:
    def __init__(self,text): self.payload=text.encode()
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def read(self): return self.payload
def req_url(r): return getattr(r,'full_url',str(r))

def test_constituent_parser_semantic_url_not_css():
    refs=parse_constituent_page('<div class="new"><a href="/borsa/azioni/scheda/IT0001233417-MTAA.html?lang=it"> A2a </a></div>')
    assert len(refs)==1 and refs[0].isin=='IT0001233417' and refs[0].name=='A2a'
def test_constituent_parser_ignores_non_equity_links():
    refs=parse_constituent_page('<a href="/borsa/indici/x">FTSE MIB</a><a href="/borsa/azioni/scheda/IT0001233417-MTAA.html?lang=it">A2a</a>')
    assert [x.isin for x in refs]==['IT0001233417']
def test_constituent_parser_isolates_table_from_broken_upstream_markup():
    html = """
    <html>
      <body>
        <script>
          const broken = "markup without a closing script tag";

        <table>
          <tbody>
            <tr>
              <td>
                <a href="/borsa/azioni/scheda/IT0001233417-MTAA.html?lang=it">
                  <span><strong>A2a</strong></span>
                </a>
              </td>
              <td>
                <a href="/borsa/azioni/scheda/IT0001233417-MTAA.html?lang=it">
                  A2a
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """

    refs = parse_constituent_page(html)

    assert len(refs) == 1
    assert refs[0].isin == "IT0001233417"
    assert refs[0].name == "A2a"
def test_detail_parser_semantic_labels():
    isin,symbol,market=parse_instrument_detail('<p>x</p><div>Codice Alfanumerico</div><div>A2A</div><section>Mercato/Segmento</section><em>Euronext Milan</em><aside>Codice Isin</aside><b>IT0001233417</b>', expected_isin='IT0001233417')
    assert (isin,symbol,market)==('IT0001233417','A2A','Euronext Milan')
def test_detail_parser_accepts_euronext_star_milan():
    result = parse_instrument_detail(
        detail_html(
            "IT0004056880",
            "AMP",
            "Euronext STAR Milan",
        ),
        expected_isin="IT0004056880",
    )

    assert result == (
        "IT0004056880",
        "AMP",
        "Euronext STAR Milan",
    )
def test_detail_parser_isolates_tables_from_broken_upstream_markup():
    html = """
    <html>
      <body>
        <script>
          const broken = "markup without a closing script tag";

        <table>
          <tr>
            <td>Mercato/Segmento</td>
            <td>Euronext Milan</td>
          </tr>
          <tr>
            <td>Codice Isin</td>
            <td>IT0001233417</td>
          </tr>
          <tr>
            <td>Codice Alfanumerico</td>
            <td>A2A</td>
          </tr>
        </table>
      </body>
    </html>
    """

    result = parse_instrument_detail(
        html,
        expected_isin="IT0001233417",
    )

    assert result == (
        "IT0001233417",
        "A2A",
        "Euronext Milan",
    )
def test_detail_parser_isin_mismatch():
    import pytest
    with pytest.raises(ValueError,match='ISIN mismatch'): parse_instrument_detail(detail_html('IT0001233417','A2A'), expected_isin='IT0009999999')
def test_detail_parser_wrong_market():
    import pytest
    with pytest.raises(ValueError,match='unexpected market'): parse_instrument_detail(detail_html('IT0001233417','A2A','Euronext Growth Milan'), expected_isin='IT0001233417')
def test_provider_builds_raw_market_listings():
    p1=[('IT0000000001','Alpha'),('IT0000000002','Beta')]; p2=[('IT0000000003','Gamma')]
    data={'https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html':membership_html(p1), 'https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html?page=2':membership_html(p2)}
    for isin,sym in [('IT0000000001','AAA'),('IT0000000002','BBB'),('IT0000000003','CCC')]: data[f'https://www.borsaitaliana.it/borsa/azioni/scheda/{isin}-MTAA.html?lang=it']=detail_html(isin,sym)
    result=BorsaItalianaFTSEMIBProvider(opener=lambda r,timeout:FakeResponse(data[req_url(r)]), expected_min_constituents=3, expected_max_constituents=3).fetch()
    assert result.status is BorsaItalianaProviderStatus.SUCCESS
    assert [x.symbol for x in result.listings]==['AAA','BBB','CCC']
    assert all(x.exchange=='BIT' and x.market=='EURONEXT_MILAN' and x.region=='EUROPE' and x.currency=='EUR' and x.country=='IT' for x in result.listings)
def test_provider_implausible_count_fails_before_details():
    calls=[]
    def opener(r,timeout): calls.append(req_url(r)); return FakeResponse(membership_html([('IT0000000001','Alpha')]))
    result=BorsaItalianaFTSEMIBProvider(opener=opener).fetch()
    assert result.status is BorsaItalianaProviderStatus.FAILED and result.diagnostics[0].code=='IMPLAUSIBLE_CONSTITUENT_COUNT' and len(calls)==2
def test_provider_partial_when_detail_breaks():
    p1=[('IT0000000001','Alpha'),('IT0000000002','Beta')]; p2=[('IT0000000003','Gamma')]
    data={'https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html':membership_html(p1),'https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html?page=2':membership_html(p2),
    'https://www.borsaitaliana.it/borsa/azioni/scheda/IT0000000001-MTAA.html?lang=it':detail_html('IT0000000001','AAA'),
    'https://www.borsaitaliana.it/borsa/azioni/scheda/IT0000000002-MTAA.html?lang=it':'<html>changed</html>',
    'https://www.borsaitaliana.it/borsa/azioni/scheda/IT0000000003-MTAA.html?lang=it':detail_html('IT0000000003','CCC')}
    result=BorsaItalianaFTSEMIBProvider(opener=lambda r,timeout:FakeResponse(data[req_url(r)]), expected_min_constituents=3, expected_max_constituents=3).fetch()
    assert result.status is BorsaItalianaProviderStatus.PARTIAL and len(result.listings)==2 and result.diagnostics[0].code=='DETAIL_REJECTED'
def test_provider_duplicate_symbol_fails_closed():
    p1=[('IT0000000001','Alpha'),('IT0000000002','Beta')]; p2=[('IT0000000003','Gamma')]
    data={'https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html':membership_html(p1),'https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html?page=2':membership_html(p2)}
    for isin,sym in [('IT0000000001','AAA'),('IT0000000002','AAA'),('IT0000000003','CCC')]: data[f'https://www.borsaitaliana.it/borsa/azioni/scheda/{isin}-MTAA.html?lang=it']=detail_html(isin,sym)
    result=BorsaItalianaFTSEMIBProvider(opener=lambda r,timeout:FakeResponse(data[req_url(r)]), expected_min_constituents=3, expected_max_constituents=3).fetch()
    assert result.status is BorsaItalianaProviderStatus.FAILED and result.diagnostics[0].code=='DUPLICATE_SYMBOL'
