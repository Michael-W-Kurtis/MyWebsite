#!/bin/bash

urls="https://game321414.konggames.com/gamez/0032/1414/live/Build/GGGGGAMERIHARDLYKNOWHER.json
https://game321414.konggames.com/gamez/0032/1414/live/Build/UnityLoader.js
https://game321414.konggames.com/gamez/0032/1414/live/Build/GGGGGAMERIHARDLYKNOWHER.wasm.code.unityweb
https://game321414.konggames.com/gamez/0032/1414/live/Build/GGGGGAMERIHARDLYKNOWHER.wasm.framework.unityweb
https://game321414.konggames.com/gamez/0032/1414/live/Build/GGGGGAMERIHARDLYKNOWHER.data.unityweb"

for url in $urls;
do 
        thing=$( echo $url |  cut -f9 -d'/' )
        echo curl $url -o $thing
	curl $url -o $thing
done
