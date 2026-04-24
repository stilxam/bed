from fx_converter import convert_to_eur

results = convert_to_eur([(100.0, "USD"), (2500.0, "JPY")])
for r in results:
    print(r.total_eur, r.source)