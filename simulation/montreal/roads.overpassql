[out:xml][timeout:25][maxsize:33554432];
way["highway"]["area"!="yes"](45.505,-73.580,45.520,-73.560)->.roads;
rel(bw.roads)["type"="restriction"]->.turns;
(.roads;.turns;);
(._;>>;);
out body;
