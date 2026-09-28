# Classification Propagation Matrix
| Operation | Default |
|---|---|
| capture | output inherits source restrictions |
| merge | conservative join of restrictions |
| diff | conservative join, possibly stricter |
| generate | restricted-derived synthetic |
| sanitize | remains restricted absent governed release |
| history | inherits restrictions |
| plugin/sink | only approved trust zone |
| Release/Transfer | explicit policy + authorization required |
