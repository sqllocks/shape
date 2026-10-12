const vscode=require('vscode'), assert=require('assert'), fs=require('fs'), cp=require('child_process');
exports.run=async()=>{
 const ext=vscode.extensions.getExtension('sqllocks.shape');assert(ext);
 assert(ext.extensionPath.startsWith('/tmp/owner-notes2-vscode-extensions/'), 'Shape must be the installed VSIX, not the development extension');
 await vscode.workspace.getConfiguration('shape').update('path','/workspace/shape/.venv/bin/shape',vscode.ConfigurationTarget.Global);
 const api=await ext.activate();
 console.log('PASS installed Shape VSIX: '+ext.packageJSON.version);
 console.log('VS Code '+vscode.version);
 const yaml=await vscode.workspace.openTextDocument('/tmp/owner-notes2-vscode-project/shape.yml');
 await vscode.window.showTextDocument(yaml);assert.equal(yaml.languageId,'yaml');
 console.log('PASS real shape.yml opened as YAML');
 const file='/tmp/owner-notes2-vscode-project/orders.shape';
 await vscode.commands.executeCommand('vscode.open',vscode.Uri.file(file));
 for(let i=0;i<50;i++){
  const input=vscode.window.tabGroups.activeTabGroup.activeTab?.input;
  if(input instanceof vscode.TabInputText && input.uri.scheme==='shape-text')break;
  await new Promise(r=>setTimeout(r,100));
 }
 const input=vscode.window.tabGroups.activeTabGroup.activeTab?.input;
 assert(input instanceof vscode.TabInputText && input.uri.scheme==='shape-text','custom editor must open the Shape text view');
 const doc=await vscode.workspace.openTextDocument(api.textUri(file,'working'));
 const cat=cp.spawnSync('/workspace/shape/.venv/bin/shape',['cat',file],{encoding:'utf8'});
 assert.equal(cat.status,0);assert.equal(doc.getText().trim(),cat.stdout.trim());
 assert(doc.getText().includes('row_count: 2'));
 await vscode.window.showTextDocument(doc);const before=doc.getText();
 await vscode.commands.executeCommand('type',{text:'receipt-probe'});
 assert.equal(doc.getText(),before);assert.equal(doc.isDirty,false);
 console.log('PASS .shape custom editor matches real shape cat; row_count 2; read-only');
};
