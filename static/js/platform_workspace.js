/* Bind legacy relative-path operations to the workspace displayed by this page. */
(function(root){
  let token=null,epoch=0,pending=false,reloading=false,ready=null,reading=null;
  const originalFetch=root.fetch.bind(root);
  function legacy(url){return url.origin===root.location.origin&&((url.pathname.startsWith('/api/')&&!url.pathname.startsWith('/api/workbench/'))||url.pathname.startsWith('/data/exports/'))}
  root.fetch=function(input,options){
    const url=new URL(typeof input==='string'?input:input.url,root.location.href);
    if(token&&legacy(url)){
      options={...options};const headers=new Headers(options.headers||(typeof input==='object'?input.headers:undefined));
      headers.set('X-Magsim-Workspace',token);options.headers=headers;
    }
    return originalFetch(input,options);
  };
  function reload(){if(!reloading){reloading=true;root.location.reload()}}
  root.getPlatformWorkspace=function(){return reloading?Promise.resolve(null):ready?Promise.resolve({...ready}):reading||root.loadWorkspace()};
  root.loadWorkspace=function(){
    const current=++epoch;
    const work=(async()=>{
    try{
      const response=await root.fetch('/api/workspace'),data=await response.json();
      if(current!==epoch||reloading)return null;
      if(!response.ok||typeof data.workspace!=='string'||!(/^[a-f0-9]{64}$/).test(data.workspace_token||''))throw Error(data.error||'请用最新启动器打开系统');
      if(token&&token!==data.workspace_token){reload();return null}
      token=data.workspace_token;ready=data;document.getElementById('ws-disp').textContent=data.workspace;return data;
    }catch(error){if(current===epoch)toast('工作目录读取失败：'+error.message,'err');return null}
    })();reading=work;work.finally(()=>{if(reading===work)reading=null});return work;
  };
  root.changeWorkspace=async function(){
    if(pending||reloading)return false;pending=true;
    try{
      const current=await root.loadWorkspace();if(!current||reloading)return false;
      const path=prompt('输入工作目录：',current.workspace);if(!path||!path.trim())return false;
      const response=await root.fetch('/api/workspace',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})}),data=await response.json();
      if(!response.ok||!data.success)throw Error(data.error||'目录切换未获确认');
      if(data.changed===true)reload();else if(data.changed===false)toast('仍为当前工作目录，输入保留','ok');else throw Error('请用最新启动器打开系统');
      return true;
    }catch(error){toast('工作目录未切换：'+error.message,'err');return false}
    finally{pending=false}
  };
})(window);
